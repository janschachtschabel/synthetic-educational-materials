"""Fine-tunes an mT5 model on full text → title, description, keywords.

One training example per material and task, with the task as source prefix (``title: ``, ``description: ``,
``keywords: ``). All examples are shuffled together, so the model sees the three tasks mixed.

Single GPU or CPU:
    python training/train_mt5.py --data hf_export --out runs/mt5-meta
Two GPUs (Linux):
    torchrun --nproc_per_node 2 training/train_mt5.py --data hf_export --out runs/mt5-meta
Pilot with a subset:
    python training/train_mt5.py --data hf_export --out runs/pilot --max-train 5000 --epochs 1

``--data`` is the folder written by export_hf.py or a dataset ID on the Hugging Face Hub. Before training, the script
prints token lengths of inputs and targets and how many would be cut at the chosen limits.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
import statistics
import sys
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="export folder from export_hf.py or Hub dataset ID")
    ap.add_argument("--model", default="deutsche-telekom/mt5-small-sum-de-mit-v1")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tasks", default="title,description,keywords")
    ap.add_argument("--input-mode", choices=["head", "condensed"], default="head",
                    help="head: plain text cut at --max-source-length; condensed: headings + first sentences")
    ap.add_argument("--max-source-length", type=int, default=768)
    ap.add_argument("--max-target-title", type=int, default=DEFAULT_TARGET_LENGTHS["title"])
    ap.add_argument("--max-target-description", type=int, default=DEFAULT_TARGET_LENGTHS["description"])
    ap.add_argument("--max-target-keywords", type=int, default=DEFAULT_TARGET_LENGTHS["keywords"])
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, default=8, help="per device")
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--warmup-ratio", type=float, default=0.05)
    ap.add_argument("--max-train", type=int, help="use only this many training materials (pilot)")
    ap.add_argument("--max-eval", type=int, default=1000, help="validation materials for the loss")
    ap.add_argument("--samples", type=int, default=50, help="test materials to generate example outputs for")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=4, help="dataloader workers")
    a = ap.parse_args(argv)

    import torch
    from transformers import (AutoModelForSeq2SeqLM, AutoTokenizer, DataCollatorForSeq2Seq, Seq2SeqTrainer,
                              Seq2SeqTrainingArguments, set_seed)
    from datasets import Dataset

    set_seed(a.seed)
    tasks = [t.strip() for t in a.tasks.split(",") if t.strip()]
    target_lengths = {"title": a.max_target_title, "description": a.max_target_description,
                      "keywords": a.max_target_keywords}
    target_lengths = {t: target_lengths[t] for t in tasks}

    splits = load_splits(a.data)
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForSeq2SeqLM.from_pretrained(a.model)

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

    cols = ["task", "source", "target", "id"]
    train_ds = Dataset.from_list(train_ex).map(tokenize, batched=True, remove_columns=cols, desc="tokenize train")
    eval_ds = Dataset.from_list(eval_ex).map(tokenize, batched=True, remove_columns=cols, desc="tokenize validation")

    # mT5 is unstable in fp16: bf16 where the GPU supports it, otherwise fp32
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    # warmup as steps (warmup_ratio is not available in all transformers versions)
    world = int(os.environ.get("WORLD_SIZE", "1"))
    steps = math.ceil(len(train_ds) / (a.batch_size * a.grad_accum * world)) * a.epochs
    warmup_steps = int(a.warmup_ratio * steps)
    kwargs = dict(
        output_dir=str(a.out),
        num_train_epochs=a.epochs,
        learning_rate=a.lr,
        per_device_train_batch_size=a.batch_size,
        per_device_eval_batch_size=a.batch_size,
        gradient_accumulation_steps=a.grad_accum,
        warmup_steps=warmup_steps,
        lr_scheduler_type="linear",
        weight_decay=0.01,
        bf16=bf16,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        logging_steps=50,
        group_by_length=True,
        dataloader_num_workers=a.workers,
        report_to="none",
        seed=a.seed,
    )
    # transformers changes options between versions: pass only what this version knows
    known = {f.name for f in dataclasses.fields(Seq2SeqTrainingArguments)}
    if skipped := sorted(set(kwargs) - known):
        print(f"Options not supported by this transformers version, skipped: {', '.join(skipped)}")
    args = Seq2SeqTrainingArguments(**{k: v for k, v in kwargs.items() if k in known})
    trainer = Seq2SeqTrainer(model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds,
                             data_collator=DataCollatorForSeq2Seq(tok, model=model),
                             processing_class=tok)
    trainer.train()

    if not trainer.is_world_process_zero():
        return 0
    final = a.out / "final"
    trainer.save_model(str(final))
    tok.save_pretrained(str(final))
    settings = {"base_model": a.model, "tasks": {t: TASKS[t] for t in tasks}, "input_mode": a.input_mode,
                "max_source_length": a.max_source_length, "max_target_lengths": target_lengths}
    (final / "metadata_generation.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    # Example outputs on unseen test topics
    if a.samples and "test" in splits:
        model = trainer.model.eval()
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


if __name__ == "__main__":
    sys.exit(main())
