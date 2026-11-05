"""Uploads the fine-tuned model and its model card to the Hugging Face Hub.

    hf auth login
    python training/upload_model.py --repo JanSchachtschabel/mt5-small-german-educational-metadata

The model folder is uploaded as-is (config, weights, tokenizer, metadata_generation.json); the card in
``training/model_card.md`` becomes the repository's README.md. ``--dry-run`` only checks the files and prints what
would be uploaded, without contacting the Hub.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REQUIRED = ("config.json", "model.safetensors", "tokenizer.json", "metadata_generation.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="target repository, e.g. user/mt5-small-german-educational-metadata")
    ap.add_argument("--model", type=Path, default=Path("runs/mt5-meta/final"))
    ap.add_argument("--card", type=Path, default=Path("training/model_card.md"))
    ap.add_argument("--private", action="store_true", help="create a private repository")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if "/" not in a.repo:
        raise SystemExit("--repo must look like 'user/repo-name'")
    for name in REQUIRED:
        if not (a.model / name).is_file():
            raise SystemExit(f"missing in {a.model}: {name}")
    if not a.card.is_file():
        raise SystemExit(f"model card not found: {a.card}")

    total = sum(p.stat().st_size for p in a.model.iterdir() if p.is_file())
    print(f"model:  {a.model} ({total / 1e9:.2f} GB, {len(list(a.model.iterdir()))} entries)")
    print(f"card:   {a.card} -> README.md")
    print(f"repo:   {a.repo} ({'private' if a.private else 'public'})")
    if a.dry_run:
        print("dry run – nothing uploaded")
        return 0

    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi()
    try:
        user = api.whoami()["name"]
    except HfHubHTTPError:
        print("Not logged in. Run 'hf auth login' (or set HF_TOKEN) and try again.", file=sys.stderr)
        return 1
    if not a.repo.startswith(user + "/"):
        print(f"Warning: the token belongs to '{user}', not '{a.repo.split('/')[0]}'.")

    api.create_repo(a.repo, repo_type="model", private=a.private, exist_ok=True)
    api.upload_folder(
        repo_id=a.repo,
        repo_type="model",
        folder_path=str(a.model),
        ignore_patterns=["training_args.bin", "README.md"],
        commit_message="Upload mT5-small metadata model (test)",
    )
    api.upload_file(
        repo_id=a.repo,
        repo_type="model",
        path_or_fileobj=str(a.card),
        path_in_repo="README.md",
        commit_message="Add model card",
    )
    print(f"done: https://huggingface.co/{a.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
