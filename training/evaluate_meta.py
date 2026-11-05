"""Scores a trained metadata model on held-out test materials (no generation training, just inference).

    python training/evaluate_meta.py --model runs/mt5-meta/final --data training/hf_export --n 200

Generates title, description and keywords for ``--n`` test materials and compares them with the references in the
dataset: exact and word-overlap match for titles, keyword F1 for keywords, ROUGE-L F1 for descriptions. Writes the
generated and the reference metadata to a JSONL file next to the scores so the outputs can be read by hand.

``--compare runs/a/final runs/b/final`` runs the same materials through several models and prints one table, which is
how a pilot model is compared with the final model.

Inference is slow (~13 s per material on one GPU), so the script prints a progress line with elapsed time and an ETA.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textprep import TASKS, prepare_input, target_text  # noqa: E402

_WORD = re.compile(r"[a-zäöüßß0-9]+", re.IGNORECASE)


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def f1_words(generated: str, reference: str) -> float:
    """Token-level F1, the usual proxy for 'did it say the same thing' on short fields."""
    g, r = Counter(words(generated)), Counter(words(reference))
    common = sum((g & r).values())
    if not common:
        return 0.0
    prec, rec = common / sum(g.values()), common / sum(r.values())
    return 2 * prec * rec / (prec + rec)


def rouge_l(generated: str, reference: str) -> float:
    """ROUGE-L F1 (word level, longest common subsequence)."""
    a, b = words(generated), words(reference)
    if not a or not b:
        return 0.0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b):
            cur[j + 1] = prev[j] + 1 if x == y else max(prev[j + 1], cur[j])
        prev = cur
    lcs = prev[-1]
    prec, rec = lcs / len(a), lcs / len(b)
    return 2 * prec * rec / (prec + rec) if lcs else 0.0


def keyword_f1(generated: str, reference: str) -> float:
    """F1 over comma-separated keyword strings; matches whole lower-case keywords."""
    def split(text: str) -> list[str]:
        return [k.strip().lower() for k in text.split(",") if k.strip()]

    g, r = Counter(split(generated)), Counter(split(reference))
    common = sum((g & r).values())
    if not common:
        return 0.0
    prec, rec = common / sum(g.values()), common / sum(r.values())
    return 2 * prec * rec / (prec + rec)


METRICS = {
    "title": lambda g, r: (1.0 if g.strip().lower() == r.strip().lower() else 0.0, f1_words(g, r)),
    "description": lambda g, r: (rouge_l(g, r), rouge_l(g, r)),
    "keywords": lambda g, r: (keyword_f1(g, r), f1_words(g, r)),
}


def generate(model, tok, source: str, max_source: int, max_new: int, num_beams: int) -> str:
    import torch

    enc = tok(source, max_length=max_source, truncation=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        ids = model.generate(**enc, max_new_tokens=max_new, num_beams=num_beams, no_repeat_ngram_size=3)
    return tok.decode(ids[0], skip_special_tokens=True)


def score_model(model_path: str, rows: list[dict], tasks: list[str], target_lengths: dict[str, int],
                input_mode: str, max_source: int, num_beams: int, samples_file: Path | None,
                model_label: str = ""):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_path)
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    model = model.to("cuda" if torch.cuda.is_available() else "cpu")
    if bf16:
        model = model.to(torch.bfloat16)
    model.eval()

    scores: dict[str, dict[str, list[float]]] = {t: {"strict": [], "loose": []} for t in tasks}
    outputs: list[dict] = []
    started = time.monotonic()
    total = len(rows)
    log_every = max(1, total // 10)  # ~10 updates when the output is redirected to a log
    interactive = sys.stdout.isatty()
    for i, r in enumerate(rows, start=1):
        source = prepare_input(r["fulltext"], input_mode)
        out = {"id": r["id"], "reference": {t: target_text(t, r) for t in tasks}, "generated": {}}
        for t in tasks:
            text = generate(model, tok, TASKS[t] + source, max_source, target_lengths[t], num_beams)
            out["generated"][t] = text
            strict, loose = METRICS[t](text, out["reference"][t])
            scores[t]["strict"].append(strict)
            scores[t]["loose"].append(loose)
        outputs.append(out)
        if interactive or i % log_every == 0 or i == total:
            elapsed = time.monotonic() - started
            eta = elapsed / i * (total - i)
            line = (f"  {model_label[:28]:28} {i:4}/{total} ({100 * i / total:5.1f} %)  "
                    f"{elapsed / 60:5.1f} min, ETA {eta / 60:5.1f} min")
            if interactive:
                print("\r" + line, end="", flush=True)
            else:
                print(line, flush=True)
    if interactive and total:
        print()
    if samples_file:
        with samples_file.open("w", encoding="utf-8") as fh:
            for out in outputs:
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return scores


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often run on cp1252; replace characters they cannot show instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", nargs="+", required=True, help="model folder(s); several folders are compared")
    ap.add_argument("--data", default="training/hf_export", help="export folder or Hub dataset ID")
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=200, help="materials of that split to score")
    ap.add_argument("--tasks", default="title,description,keywords")
    ap.add_argument("--input-mode", choices=["head", "condensed"], default=None,
                    help="default: read from metadata_generation.json of the first model")
    ap.add_argument("--max-source-length", type=int, default=None)
    ap.add_argument("--num-beams", type=int, default=4)
    ap.add_argument("--out", type=Path, help="write generated/reference metadata here (JSONL)")
    a = ap.parse_args(argv)

    from train_mt5 import load_splits

    splits = load_splits(a.data)
    if a.split not in splits:
        raise SystemExit(f"split {a.split!r} not in {a.data} (have: {', '.join(splits)})")
    rows = list(splits[a.split])[: a.n]

    # settings used for training, so evaluation uses the same input and the same limits
    meta = json.loads((Path(a.model[0]) / "metadata_generation.json").read_text(encoding="utf-8")) \
        if (Path(a.model[0]) / "metadata_generation.json").exists() else {}
    tasks = [t.strip() for t in a.tasks.split(",") if t.strip()]
    input_mode = a.input_mode or meta.get("input_mode", "head")
    max_source = a.max_source_length or meta.get("max_source_length", 768)
    target_lengths = {**{"title": 48, "description": 256, "keywords": 96}, **meta.get("max_target_lengths", {})}
    target_lengths = {t: target_lengths[t] for t in tasks}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)

    def samples_for(path: str) -> Path | None:
        if not a.out:
            return None
        return a.out if len(a.model) == 1 else a.out.with_name(f"{a.out.stem}-{Path(path).name}.jsonl")

    header = f"{'model':38} {'n':>4}  " + "  ".join(f"{t:>16}" for t in tasks)
    print(f"{a.split} split, {len(rows)} materials, input mode {input_mode}, max source {max_source}\n")
    print(header)
    print("-" * len(header))
    for path in a.model:
        name = Path(path).name or path
        scores = score_model(path, rows, tasks, target_lengths, input_mode, max_source, a.num_beams,
                             samples_for(path), model_label=name)
        cells = []
        for t in tasks:
            strict, loose = statistics.mean(scores[t]["strict"]), statistics.mean(scores[t]["loose"])
            cells.append(f"{strict:6.3f}/{loose:5.3f}")
        print(f"{name[:38]:38} {len(rows):4}  " + "  ".join(f"{c:>16}" for c in cells))
    print("\nstrict: exact title match / ROUGE-L F1 (description) / keyword F1 · loose: word-overlap F1")
    if a.out:
        print(f"outputs: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
