"""Fine-tunes an mT5 model on full text → title, description, keywords.

One training example per material and task, with the task as source prefix (``title:``, ``description:``,
``keywords:``). All examples are shuffled together, so the model sees the three tasks mixed.

One GPU (Windows and Linux)::

    python training/train_mt5.py --data training/hf_export --out runs/mt5-meta

Two GPUs on Windows (single process, the Trainer wraps the model in DataParallel)::

    python training/train_mt5.py --data training/hf_export --out runs/mt5-meta

Two GPUs on Linux (real DDP, fastest)::

    torchrun --nproc_per_node 2 training/train_mt5.py --data training/hf_export --out runs/mt5-meta

Pilot with a subset (measures throughput, then stop and start the full run)::

    python training/train_mt5.py --data training/hf_export --out runs/pilot --max-train 5000 --epochs 1

``--data`` is the folder written by export_hf.py, a checkout of the Hub dataset, or a Hub dataset ID. Before training
the script prints token lengths of inputs and targets with the share that would be cut, sizes the batch for the GPU
(a probe over batch sizes 1…48 that measures several steps including the optimizer state, on by default on CUDA,
``--no-find-batch`` skips it) and estimates the wall-clock time
for the planned epochs.

``--gpu 1`` restricts the run to one physical GPU (use it while the other GPU is in use by another program).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textprep import TASKS, prepare_input, target_text  # noqa: E402

DEFAULT_TARGET_LENGTHS = {"title": 48, "description": 256, "keywords": 96}


def load_splits(data: str):
    from datasets import load_dataset

    p = Path(data)
    if p.is_dir():
        files = {s: str(p / "data" / f"{s}-*.parquet") for s in ("train", "validation", "test")
                 if list((p / "data").glob(f"{s}-*.parquet"))}
        if not files:
            raise SystemExit(f"no parquet files under {p / 'data'} – run export_hf.py first or pass a Hub dataset ID")
        return load_dataset("parquet", data_files=files)
    return load_dataset(data)


def make_examples(ds, tasks: list[str], input_mode: str, limit: int | None, seed: int) -> list[dict]:
    rows = list(ds)
    if limit and limit < len(rows):
        rows = random.Random(seed).sample(rows, limit)
    out = []
    for r in rows:
        source = prepare_input(r["fulltext"], input_mode)
        for task in tasks:
            out.append({"task": task, "source": TASKS[task] + source, "target": target_text(task, r), "id": r["id"]})
    random.Random(seed).shuffle(out)
    return out


def length_report(tok, examples: list[dict], max_source: int, target_lengths: dict[str, int], n: int = 2000) -> None:
    sample = examples[:n]
    src = [len(tok(e["source"]).input_ids) for e in sample]
    print(f"\nToken lengths (sample of {len(sample)} examples): median / 95th percentile / max, share cut")
    print(f"  input        {statistics.median(src):6.0f} / {sorted(src)[int(.95 * (len(src) - 1))]:5d} / "
          f"{max(src):5d}   {100 * sum(s > max_source for s in src) / len(src):5.1f} % > {max_source}")
    for task, limit in target_lengths.items():
        t = [len(tok(text_target=e["target"]).input_ids) for e in sample if e["task"] == task]
        if t:
            print(f"  {task:12} {statistics.median(t):6.0f} / {sorted(t)[int(.95 * (len(t) - 1))]:5d} / "
                  f"{max(t):5d}   {100 * sum(x > limit for x in t) / len(t):5.1f} % > {limit}")
    print()


def report_lengths(tok, rows: list[dict], limits: list[int]) -> None:
    """Prints token lengths of both input modes and the share that survives each limit (--report-only).

    Encodes the longest task prefix plus the text, exactly as training does, so the shares are the real ones
    (the prefix and the end-of-text token count towards --max-source-length).
    """
    sample = rows[:2000]
    prefix = max(TASKS.values(), key=lambda p: len(tok(p).input_ids))
    src = {mode: [len(tok(prefix + prepare_input(r["fulltext"], mode)).input_ids) for r in sample]
           for mode in ("head", "condensed")}
    print(f"\nFull text in mT5 tokens, with the task prefix and the end token (sample of {len(sample)} materials)")
    print(f"{'mode':10} {'median':>7} {'p90':>6} {'p95':>6} {'max':>6}   "
          + "   ".join(f"fits <= {lim}" for lim in limits))
    for mode, lengths in src.items():
        s = sorted(lengths)
        share = "   ".join(f"{100 * sum(x <= lim for x in s) / len(s):11.1f} %" for lim in limits)
        print(f"{mode:10} {statistics.median(s):7.0f} {s[int(.9 * (len(s) - 1))]:6} {s[int(.95 * (len(s) - 1))]:6} "
              f"{s[-1]:6}   {share}")
    targets = {"title": [len(tok(text_target=r["title"]).input_ids) for r in sample],
               "description": [len(tok(text_target=r["description"]).input_ids) for r in sample],
               "keywords": [len(tok(text_target=", ".join(r["keywords"])).input_ids) for r in sample]}
    print("\nTarget fields in tokens")
    for name, lengths in targets.items():
        s = sorted(lengths)
        print(f"  {name:12} median {statistics.median(s):5.0f}   p90 {s[int(.9 * (len(s) - 1))]:5}   "
              f"p95 {s[int(.95 * (len(s) - 1))]:5}   max {s[-1]:5}")
    print()


def tokenized_cache_dir(out: Path, settings: dict) -> Path:
    """Cache folder for the tokenized examples; reused only when the settings match."""
    key = hashlib.sha1(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return out / "tokenized" / key


def find_batch_size(model, tok, examples: list[dict], max_source: int, target_lengths: dict[str, int],
                    seed: int, bf16: bool, extra_margin: float = 0.0) -> int | None:
    """Largest per-device batch that fits, measured over several steps with real length variation.

    A single step is not enough: with variable sequence lengths the caching allocator keeps blocks of several
    sizes, so the reserved memory of the real loop keeps growing for a few steps (on Windows there is no
    ``expandable_segments``). The probe therefore measures the peak over several steps, with the optimizer state
    already allocated, which is the memory the training loop actually needs. ``extra_margin`` GiB stay free for
    what comes on top (DataParallel gathers the outputs of all GPUs on the first one). Returns None when even one
    example does not fit.
    """
    import torch
    from torch.nn.utils import clip_grad_norm_
    from torch.optim import AdamW
    from transformers import DataCollatorForSeq2Seq

    collator = DataCollatorForSeq2Seq(tok, model=model, pad_to_multiple_of=8)
    device = model.device
    pool = [{"input_ids": tok(e["source"], max_length=max_source, truncation=True).input_ids,
             "labels": tok(text_target=e["target"], max_length=target_lengths[e["task"]], truncation=True).input_ids}
            for e in random.Random(seed).sample(examples, min(600, len(examples)))]
    random.Random(seed).shuffle(pool)  # random length variation, as the shuffled training loop sees it
    steps = 6  # enough for the allocator to reach its steady-state fragmentation

    free_at_start = torch.cuda.mem_get_info(device)[0] if device.type == "cuda" else 0
    margin = (1.5 + extra_margin) * 2**30
    usable = free_at_start - margin if device.type == "cuda" else float("inf")
    print(f"Probe: {steps} steps per batch size over {len(pool)} examples with varied lengths, "
          f"{free_at_start / 2**30:.1f} GiB free, using at most {(usable / 2**30):.1f} GiB")

    def chunks(size: int):
        for step in range(steps):
            start = (step * size) % len(pool)
            chunk = pool[start:start + size]
            if len(chunk) < size:
                chunk = pool[:size]
            yield chunk

    def trial(size: int) -> bool:
        # Start from a clean allocator cache, so the previous trial's cached blocks do not hide the real peak.
        model.zero_grad(set_to_none=True)
        if device.type == "cuda":
            torch.cuda.empty_cache()
        base = torch.cuda.memory_reserved(device) if device.type == "cuda" else 0
        opt = AdamW(model.parameters(), lr=0.0, weight_decay=0.0)  # lr=0: state is allocated, weights stay
        autocast = torch.autocast("cuda", dtype=torch.bfloat16, enabled=bf16) if device.type == "cuda" \
            else contextlib.nullcontext()
        try:
            # AdamW creates two fp32 tensors per parameter on its first step and keeps them for the whole run.
            # They are resident during every later forward/backward, so allocate them first (with the smallest
            # batch) and only then measure. Stopping at the backward – or stepping after it – misses these
            # ~2.5 GiB and made the old probe pick a batch that later ran out of memory.
            warm = collator(pool[:1]).to(device)
            with autocast:
                warm_loss = model(**warm).loss
            warm_loss.backward()
            opt.step()
            opt.zero_grad(set_to_none=True)
            del warm, warm_loss
            if device.type == "cuda":
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats(device)

            for chunk in chunks(size):
                batch = collator(chunk).to(device)
                with autocast:  # same setting as training
                    loss = model(**batch).loss
                loss.backward()
                clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
            if device.type == "cuda":
                peak = torch.cuda.max_memory_reserved(device) - base
                if peak > usable:
                    print(f"  batch {size}: needs {peak / 2**30:.1f} GiB – over the budget")
                    return False
                print(f"  batch {size}: {peak / 2**30:.1f} GiB")
            return True
        except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:  # OOM shows up as both
            if "out of memory" not in str(exc).lower():
                raise
            return False
        finally:
            model.zero_grad(set_to_none=True)
            del opt

    was_training = model.training
    model.train()  # gradient checkpointing only takes effect in train mode, exactly as under the Trainer
    try:
        best = None
        for size in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48):
            if trial(size):
                best = size
            else:
                break
    finally:
        model.train(was_training)
    model.zero_grad(set_to_none=True)
    if model.device.type == "cuda":
        torch.cuda.empty_cache()
    return best


def time_training(model, tok, train_ds, batch_size: int, grad_accum: int, steps: int, bf16: bool) -> tuple[float, float]:
    """Runs a few real training steps; returns examples per second and the loss of the first step.

    The first loss doubles as a health check: a text-to-text model that starts from chance sits around ln(vocabulary)
    ≈ 12.4, the fine-tuned mT5 base here starts near 6. A clearly higher value means the weights did not load.
    """
    import torch
    from torch.optim import AdamW
    from torch.utils.data import DataLoader
    from transformers import DataCollatorForSeq2Seq

    device = next(model.parameters()).device
    # lr=0: the measurement must not change the weights the real training then starts from; the optimizer work
    # (and therefore the measured speed and memory) is the same as with the real learning rate.
    opt = AdamW(model.parameters(), lr=0.0)
    dl = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                    collate_fn=DataCollatorForSeq2Seq(tok, model=model, pad_to_multiple_of=8),
                    num_workers=0, pin_memory=device.type == "cuda")
    model.train()
    opt.zero_grad(set_to_none=True)
    if device.type == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)
    done, started, first_loss = 0, time.time(), float("nan")
    for step, batch in enumerate(dl):
        batch = {k: v.to(device) for k, v in batch.items()}
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=bf16 and device.type == "cuda"):
            # only keep the scalar loss: a kept ModelOutput would pin the logits of the previous step
            # (about 1 GiB at this vocabulary) and make the estimate need more memory than real training
            loss = model(**batch).loss
        if math.isnan(first_loss):
            first_loss = loss.item()
        (loss / grad_accum).backward()
        if (step + 1) % grad_accum == 0:
            opt.step()
            opt.zero_grad(set_to_none=True)
        done += batch["input_ids"].shape[0]
        if step + 1 >= steps:
            break
    elapsed = time.time() - started
    if device.type == "cuda":
        print(f"Peak GPU memory during the measured steps: "
              f"{torch.cuda.max_memory_reserved(device) / 2**30:.1f} GiB")
    model.zero_grad(set_to_none=True)
    del opt, dl
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return (done / elapsed if elapsed > 0 else 0.0), first_loss


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often run on cp1252, where arrows/ellipses in the progress output would crash the
    # script with UnicodeEncodeError. Keep the encoding, just replace characters it cannot show.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="export folder from export_hf.py or Hub dataset ID")
    ap.add_argument("--model", default="deutsche-telekom/mt5-small-sum-de-mit-v1")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tasks", default="title,description,keywords")
    ap.add_argument("--input-mode", choices=["head", "condensed"], default="head",
                    help="head: plain text cut at --max-source-length; condensed: headings + first sentences")
    ap.add_argument("--max-source-length", type=int, default=1024,
                    help="head mode needs ~920 tokens for the median text in this dataset, so 768 would cut almost "
                         "everything; condensed mode fits 512")
    ap.add_argument("--max-target-title", type=int, default=DEFAULT_TARGET_LENGTHS["title"])
    ap.add_argument("--max-target-description", type=int, default=DEFAULT_TARGET_LENGTHS["description"])
    ap.add_argument("--max-target-keywords", type=int, default=DEFAULT_TARGET_LENGTHS["keywords"])
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, help="per device; default: measured on the GPU (8 on CPU)")
    ap.add_argument("--no-find-batch", action="store_true", help="skip the GPU batch-size probe")
    ap.add_argument("--gpu", help="only use this GPU, e.g. --gpu 1 (sets CUDA_VISIBLE_DEVICES before torch starts)")
    ap.add_argument("--grad-checkpointing", action="store_true",
                    help="trade ~30 %% speed for much less memory; use if the probe finds a small batch")
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--warmup-ratio", type=float, default=0.03)
    ap.add_argument("--max-train", type=int, help="use only this many training materials (pilot)")
    ap.add_argument("--max-eval", type=int, default=1000, help="validation materials for the loss")
    ap.add_argument("--estimate-steps", type=int, default=30, help="real steps used to measure speed (0 skips)")
    ap.add_argument("--estimate-only", action="store_true",
                    help="size the batch and measure the speed, then stop without training")
    ap.add_argument("--report-only", action="store_true",
                    help="print token lengths of both input modes and stop (no training)")
    ap.add_argument("--samples", type=int, default=50, help="test materials to generate example outputs for")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, help="dataloader workers (default: 4 on Linux, 0 on Windows)")
    ap.add_argument("--resume", action="store_true", help="continue from the newest checkpoint in --out")
    a = ap.parse_args(argv)

    if a.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(a.gpu)
    if os.name != "nt":
        # fewer fragmentation stalls with the variable sequence lengths (not supported by the Windows CUDA build)
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    import torch
    from transformers import (AutoModelForSeq2SeqLM, AutoTokenizer, DataCollatorForSeq2Seq, Seq2SeqTrainer,
                              Seq2SeqTrainingArguments, set_seed)
    from datasets import Dataset

    set_seed(a.seed)
    tasks = [t.strip() for t in a.tasks.split(",") if t.strip()]
    unknown = [t for t in tasks if t not in TASKS]
    if unknown:
        raise SystemExit(f"unknown task(s): {', '.join(unknown)} – known: {', '.join(TASKS)}")
    target_lengths = {"title": a.max_target_title, "description": a.max_target_description,
                      "keywords": a.max_target_keywords}
    target_lengths = {t: target_lengths[t] for t in tasks}

    if torch.cuda.is_available():
        # TF32 for the few fp32 ops; bf16 autocast is set through the training arguments below
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        for i in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(i)
            print(f"GPU {i}: {torch.cuda.get_device_name(i)}, {total / 2**30:.1f} GiB total, "
                  f"{free / 2**30:.1f} GiB free")
    world = int(os.environ.get("WORLD_SIZE", "1"))

    splits = load_splits(a.data)
    tok = AutoTokenizer.from_pretrained(a.model)
    if a.report_only:
        report_lengths(tok, list(splits["train"]), limits=[512, 768, 1024, 1280])
        return 0
    # Guard: a base model that does not load completely would silently train from random layers. This happened once,
    # when tie_word_embeddings was set on the config before loading (transformers 5 then expects encoder/decoder
    # embed_tokens keys that the checkpoint stores once as shared.weight, and initializes them at random).
    model, load_info = AutoModelForSeq2SeqLM.from_pretrained(a.model, output_loading_info=True)
    if missing := sorted(load_info.get("missing_keys", ())):
        raise SystemExit(f"the base model loaded with missing weights: {missing[:4]} – "
                         f"training would start from random layers, so this stops here")
    model.config.tie_word_embeddings = False  # as in the base model; set after loading, never before
    if a.grad_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False

    train_ex = make_examples(splits["train"], tasks, a.input_mode, a.max_train, a.seed)
    eval_ex = make_examples(splits["validation"], tasks, a.input_mode, a.max_eval, a.seed)
    print(f"Training examples: {len(train_ex):,} ({len(train_ex) // len(tasks):,} materials × {len(tasks)} tasks), "
          f"validation: {len(eval_ex):,}")
    length_report(tok, train_ex, a.max_source_length, target_lengths)

    def tokenize(batch):
        enc = tok(batch["source"], max_length=a.max_source_length, truncation=True)
        labels = []
        for task, target in zip(batch["task"], batch["target"]):
            labels.append(tok(text_target=target, max_length=target_lengths[task], truncation=True).input_ids)
        enc["labels"] = labels
        return enc

    # tokenizing 150k examples costs a few minutes; cache the result under the settings that produced it
    cache_settings = {"model": a.model, "tasks": tasks, "input_mode": a.input_mode, "seed": a.seed,
                      "max_source_length": a.max_source_length, "max_target_lengths": target_lengths,
                      "max_train": a.max_train, "max_eval": a.max_eval, "n_train": len(train_ex)}
    cache_dir = tokenized_cache_dir(a.out, cache_settings)
    cols = ["task", "source", "target", "id"]
    if cache_dir.exists():
        print(f"Reusing tokenized examples: {cache_dir}")
        train_ds, eval_ds = (load_disk_dataset(cache_dir / name) for name in ("train", "validation"))
    else:
        train_ds = Dataset.from_list(train_ex).map(tokenize, batched=True, num_proc=4, remove_columns=cols,
                                                  desc="tokenize train")
        eval_ds = Dataset.from_list(eval_ex).map(tokenize, batched=True, num_proc=4, remove_columns=cols,
                                                 desc="tokenize validation")
        for name, ds in (("train", train_ds), ("validation", eval_ds)):
            ds.save_to_disk(str(cache_dir / name))
        print(f"Tokenized examples cached: {cache_dir}")

    # mT5 is unstable in fp16: bf16 where the GPU supports it, otherwise fp32
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    print(f"Precision: {'bf16' if bf16 else 'fp32'}")
    # Without torchrun the Trainer uses nn.DataParallel for several visible GPUs. The per-device batch size stays per
    # GPU there, but GPU 0 additionally gathers the outputs of all GPUs – with a 250,000 piece vocabulary those logits
    # are gigabytes, so the probe has to keep more distance to the memory limit.
    n_gpu = max(1, torch.cuda.device_count()) if world == 1 else 1
    per_gpu = a.batch_size
    if torch.cuda.is_available():
        model.to(f"cuda:{int(os.environ.get('LOCAL_RANK', '0'))}")  # the probe and the speed test need it on a GPU
        if per_gpu is None:
            if a.no_find_batch:
                per_gpu = 8
            else:
                print("Sizing the batch for this GPU (several steps with varied lengths, optimizer state included) …")
                extra = 2.0 if n_gpu > 1 else 0.0
                found = find_batch_size(model, tok, train_ex, a.max_source_length, target_lengths, a.seed, bf16, extra)
                if found is None:
                    raise SystemExit("Not even one example fits on this GPU – other programs hold the memory. "
                                     "Check nvidia-smi, free the card or pick a free one with --gpu 0 / --gpu 1.")
                per_gpu = found
                print(f"Batch size that fits on one GPU: {per_gpu}")
                if per_gpu <= 2 and not a.grad_checkpointing:
                    print("That is a small batch. --grad-checkpointing needs much less activation memory and "
                          "usually allows a larger batch; measure it with --estimate-only --grad-checkpointing.")
    per_gpu = per_gpu or 8
    examples_per_step = per_gpu * (n_gpu if world == 1 else world) * a.grad_accum

    if a.estimate_steps:
        rate, first_loss = time_training(model, tok, train_ds, per_gpu, a.grad_accum, a.estimate_steps, bf16)
        devices = n_gpu if world == 1 else world
        total_rate = rate * devices * (0.85 if devices > 1 else 1.0)
        hours = len(train_ds) * a.epochs / total_rate / 3600 if total_rate else float("nan")
        if int(os.environ.get("RANK", "0")) == 0:
            print(f"Measured speed: {rate:,.0f} examples/s per GPU (batch {per_gpu}) → about {hours:.1f} h for "
                  f"{a.epochs} epochs on {devices} GPU(s); one GPU alone: "
                  f"{len(train_ds) * a.epochs / rate / 3600:.1f} h")
            print(f"Health check, loss of the first steps: {first_loss:.2f} "
                  f"(chance level is 12.4; a correctly loaded base model starts clearly below, here between 3 and 7)")
            if first_loss > 11:
                print("WARNING: that loss is at chance level – the base model did not load correctly.")
    if a.estimate_only:
        if int(os.environ.get("RANK", "0")) == 0:
            print(f"Measured only, nothing trained: batch {per_gpu} per GPU on {n_gpu if world == 1 else world} device(s)")
        return 0

    steps_per_epoch = math.ceil(len(train_ds) / examples_per_step)
    steps = int(steps_per_epoch * a.epochs)
    warmup_steps = int(a.warmup_ratio * steps)
    kwargs = dict(
        output_dir=str(a.out),
        num_train_epochs=a.epochs,
        learning_rate=a.lr,
        per_device_train_batch_size=per_gpu,
        per_device_eval_batch_size=max(1, per_gpu // 2),
        gradient_accumulation_steps=a.grad_accum,
        warmup_steps=warmup_steps,
        lr_scheduler_type="linear",
        weight_decay=0.01,
        bf16=bf16,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=3,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        logging_steps=25,
        optim="adamw_torch_fused",
        dataloader_pin_memory=True,
        dataloader_num_workers=a.workers if a.workers is not None else (0 if os.name == "nt" else 4),
        report_to="none",
        seed=a.seed,
    )
    if os.name == "nt" and world > 1:
        kwargs["dataloader_num_workers"] = 0  # spawn per worker is slow under torchrun on Windows
        # NCCL has no Windows build; gloo works with CUDA tensors but is slower than NCCL

    # transformers changes options between versions: pass only what this version knows
    known = {f.name for f in dataclasses.fields(Seq2SeqTrainingArguments)}
    if "train_sampling_strategy" in known:  # transformers ≥ 5
        # batch_rebalance balances padded token cost across the DataParallel GPUs and the accumulation steps
        kwargs["train_sampling_strategy"] = "batch_rebalance" if n_gpu > 1 else "group_by_length"
    else:
        kwargs["group_by_length"] = True
    if os.name == "nt" and world > 1 and "ddp_backend" in known:
        kwargs["ddp_backend"] = "gloo"
    if skipped := sorted(set(kwargs) - known):
        print(f"Options not supported by this transformers version, skipped: {', '.join(skipped)}")
    args = Seq2SeqTrainingArguments(**{k: v for k, v in kwargs.items() if k in known})

    trainer = Seq2SeqTrainer(model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds,
                             data_collator=DataCollatorForSeq2Seq(tok, model=model, pad_to_multiple_of=8),
                             processing_class=tok)
    resume_from = None
    if a.resume:
        from transformers.trainer_utils import get_last_checkpoint

        resume_from = get_last_checkpoint(str(a.out))
        print(f"Resuming from {resume_from}" if resume_from else f"No checkpoint in {a.out}, starting fresh")
    trainer.train(resume_from)

    if not trainer.is_world_process_zero():
        return 0
    final = a.out / "final"
    trainer.save_model(str(final))
    tok.save_pretrained(str(final))
    settings = {"base_model": a.model, "tasks": {t: TASKS[t] for t in tasks}, "input_mode": a.input_mode,
                "max_source_length": a.max_source_length, "max_target_lengths": target_lengths,
                "epochs": a.epochs, "learning_rate": a.lr, "batch_per_gpu": per_gpu,
                "devices": n_gpu if world == 1 else world,
                "grad_accum": a.grad_accum, "seed": a.seed, "bf16": bf16,
                "train_examples": len(train_ds), "trainable_parameters": sum(p.numel() for p in model.parameters())}
    (final / "metadata_generation.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    # Example outputs on unseen test topics
    if a.samples and "test" in splits:
        model = trainer.model
        model = model.module if hasattr(model, "module") else model  # DataParallel wraps the model on 2 GPUs
        model = model.eval()
        test = list(splits["test"])[: a.samples]
        with (a.out / "samples_test.jsonl").open("w", encoding="utf-8") as fh:
            for r in test:
                source = prepare_input(r["fulltext"], a.input_mode)
                out = {"id": r["id"], "reference": {t: target_text(t, r) for t in tasks}, "generated": {}}
                for t in tasks:
                    enc = tok(TASKS[t] + source, max_length=a.max_source_length, truncation=True,
                              return_tensors="pt").to(model.device)
                    with torch.no_grad():
                        ids = model.generate(**enc, max_new_tokens=target_lengths[t], num_beams=4,
                                             no_repeat_ngram_size=3)
                    out["generated"][t] = tok.decode(ids[0], skip_special_tokens=True)
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")
        print(f"Example outputs: {a.out / 'samples_test.jsonl'}")
    print(f"Model saved: {final}")
    return 0


def load_disk_dataset(path: Path):
    from datasets import load_from_disk

    return load_from_disk(str(path))


if __name__ == "__main__":
    sys.exit(main())
