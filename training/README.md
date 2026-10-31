# mT5 fine-tuning: full text → title, description, keywords

*English · [Deutsch](README.de.md)*

Sample scripts to build a Hugging Face dataset from the generated materials and to train a small text-to-text model
(default: [`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1))
that generates metadata from full texts. The trained model then runs on CPU.

| File | Purpose |
|---|---|
| `export_hf.py` | materials JSONL → flat dataset (Parquet, splits by topic) with English dataset card |
| `dataset_card.md` | dataset card template; `export_hf.py` fills in the numbers |
| `train_mt5.py` | fine-tuning with source prefixes `title: `, `description: `, `keywords: ` |
| `predict.py` | generate metadata for a text file, with time per field |
| `textprep.py` | shared input preprocessing (same for training and use) |

## 1. Install

```
pip install torch --index-url https://download.pytorch.org/whl/cu124   # matching your CUDA version
pip install -r training/requirements.txt
```

## 2. Export and upload the dataset

```
python training/export_hf.py output/materialien_….jsonl --out hf_export
huggingface-cli login
huggingface-cli upload JanSchachtschabel/synthetic-german-educational-materials-50k hf_export . --repo-type dataset
```

Only the fields useful for training are exported, flattened and with English column names. Provenance, generation
details and topic labels from sources kept locally only (learning fields, educational standards, curricula) are left
out. Train/validation/test are split by topic (90/5/5), so test topics do not occur in training. Read
`hf_export/README.md` before uploading; the license is given there as CC BY 4.0.

## 3. Train

```
# pilot: 5,000 materials, 1 epoch
python training/train_mt5.py --data hf_export --out runs/pilot --max-train 5000 --epochs 1

# full, two GPUs (Linux)
torchrun --nproc_per_node 2 training/train_mt5.py --data hf_export --out runs/mt5-meta
```

Instead of `hf_export`, the Hub ID of the uploaded dataset works too. The script creates one example per material and
task and shuffles all of them. Before training it shows the token lengths of input and targets and how much is cut at
the chosen limits.

Important options:

| Option | Default | Note |
|---|---|---|
| `--max-source-length` | 768 | the base model was trained up to 800; 512 is faster on CPU |
| `--max-target-title/-description/-keywords` | 48 / 256 / 96 | descriptions have a median of about 800 characters, more than the base model's 96 tokens |
| `--input-mode` | `head` | `condensed`: headings + first sentences per section, for longer real texts |
| `--epochs`, `--lr` | 3, 1e-4 | with 150,000 examples, 2–3 epochs are enough |
| `--batch-size`, `--grad-accum` | 8, 2 | per GPU |

On GPUs with bf16 (Ampere or newer) training uses bf16, otherwise fp32; fp16 is unstable with mT5. On Windows,
`torchrun` with two GPUs usually does not work (no NCCL); use one GPU or WSL2 there.

Result: `runs/…/final/` (model, tokenizer, `metadata_generation.json` with prefixes and lengths) and
`runs/…/samples_test.jsonl` with generated and reference metadata for 50 test materials.

## 4. Use

```
python training/predict.py --model runs/mt5-meta/final text.txt
python training/predict.py --model runs/mt5-meta/final --num-beams 1 text.txt   # faster
```

Clean real web pages first (navigation, cookie notices, footers). Training texts are about one A4 page long; for
longer texts, compare `head` and `condensed`.
