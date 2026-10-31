"""Generates title, description and keywords for a text with a fine-tuned model, on CPU by default.

    python training/predict.py --model runs/mt5-meta/final text.txt
    python training/predict.py --model runs/mt5-meta/final --num-beams 1 text.txt   # faster (greedy)

Uses the input mode and length limits stored with the model (metadata_generation.json), so input is prepared the
same way as in training. Prints the time per field.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from textprep import TASKS, prepare_input  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", type=Path, help="text file (plain text or Markdown)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--num-beams", type=int, default=4)
    ap.add_argument("--threads", type=int, help="CPU threads (default: PyTorch default)")
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args(argv)

    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    if a.threads:
        torch.set_num_threads(a.threads)
    settings_file = Path(a.model) / "metadata_generation.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    tasks = settings.get("tasks", TASKS)
    lengths = settings.get("max_target_lengths", {"title": 48, "description": 256, "keywords": 96})
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForSeq2SeqLM.from_pretrained(a.model).to(a.device).eval()

    source = prepare_input(a.text.read_text(encoding="utf-8"), settings.get("input_mode", "head"))
    for task, prefix in tasks.items():
        start = time.perf_counter()
        enc = tok(prefix + source, max_length=settings.get("max_source_length", 768), truncation=True,
                  return_tensors="pt").to(a.device)
        with torch.no_grad():
            ids = model.generate(**enc, max_new_tokens=lengths.get(task, 128), num_beams=a.num_beams,
                                 no_repeat_ngram_size=3)
        text = tok.decode(ids[0], skip_special_tokens=True)
        print(f"{task} ({time.perf_counter() - start:.1f} s): {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
