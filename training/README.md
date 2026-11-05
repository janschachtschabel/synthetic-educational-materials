# mT5 fine-tuning: full text → title, description, keywords

*English · [Deutsch](README.de.md)*

Sample scripts to build a Hugging Face dataset from the generated materials and to train a small text-to-text model
(default: [`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1))
that generates metadata from full texts. The trained model also runs on CPU.

Measured numbers for the dataset, token lengths, compute time and hardware: **[TRAINING_NOTES.md](TRAINING_NOTES.md)**.

| File | Purpose |
|---|---|
| `export_hf.py` | materials JSONL → flat dataset (Parquet, splits by topic) with English dataset card |
| `dataset_card.md` | dataset card template; `export_hf.py` fills in the numbers |
| `train_mt5.py` | fine-tuning with source prefixes `title: `, `description: `, `keywords: ` |
| `evaluate_meta.py` | compare models on test materials: title matches, keyword F1, ROUGE-L |
| `predict.py` | generate metadata for a text file, with time per field |
| `demo_ui.py` | small test window: paste text or load one of 5 samples, generate any field or all three |
| `upload_model.py` | upload the final model and its model card to the Hugging Face Hub |
| `textprep.py` | shared input preprocessing (same for training and use) |

## 1. Install

Install PyTorch built for the CUDA version of the GPU. The RTX 50 series (compute capability 12.0) needs CUDA 12.8
wheels; older builds do not know that chip.

```
uv venv --python 3.13 .venv-train
uv pip install --python .venv-train\Scripts\python.exe "torch>=2.7" --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-train\Scripts\python.exe -r training\requirements.txt
```

Without `uv`: `python -m venv .venv-train` and the `pip` of that environment.

## 2. Get the dataset

Export your own materials and upload them:

```
python training/export_hf.py output/materialien_….jsonl --out training/hf_export
huggingface-cli login
huggingface-cli upload JanSchachtschabel/synthetic-german-educational-materials-50k training/hf_export . --repo-type dataset
```

or download the published dataset (Parquet, 149 MB, no login needed):

```
huggingface-cli download JanSchachtschabel/synthetic-german-educational-materials-50k --repo-type dataset --local-dir training/hf_export
```

Only the fields useful for training are exported, flattened and with English column names. Provenance, generation
details and topic labels from sources kept locally only (learning fields, educational standards, curricula) are left
out. Train/validation/test are split by topic (90/5/5), so test topics do not occur in training:
45,144 / 2,463 / 2,393 materials.

## 3. Train

```
# look at the lengths: how much full text fits into 512 / 768 / 1024 / 1280 tokens (does not train)
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --report-only

# only measure: which batch fits on the card and how long the run takes (no training)
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --estimate-only

# pilot: 5,000 materials, 1 epoch
python training/train_mt5.py --data training/hf_export --out runs/pilot --max-train 5000 --epochs 1

# full run: 45,144 materials × 3 tasks, 3 epochs
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --epochs 3
```

`--data` is the folder written by `export_hf.py` (including the downloaded one) or a Hub dataset ID. The script creates
one example per material and task and shuffles all of them. Before training it prints the token lengths, then the batch
size the GPU can fit and the **estimated total time** (measured over 30 real steps) – abort and restart with
`--input-mode condensed --max-source-length 512` if that is too long.

Important options:

| Option | Default | Note |
|---|---|---|
| `--max-source-length` | 1024 | full texts need a median of 922 tokens (with the prefix); only 1.4 % fit in 768, 84.7 % fit completely in 1024 |
| `--input-mode` | `head` | `condensed`: headings + first sentences per section, median 145 tokens, about half the compute |
| `--max-target-title/-description/-keywords` | 48 / 256 / 96 | covers 99 % of the fields |
| `--epochs`, `--lr` | 3, 1e-4 | validation loss flattens after two epochs |
| batch per card | measured | the probe measures several steps with varied lengths **including the optimizer state** and keeps 1.5 GiB below the limit (3.5 GiB with two cards); `--batch-size N` fixes it |
| `--grad-checkpointing` | – | ~30 % slower per step but much less activation memory; on 16 GB cards often the difference between batch 2 and batch 6 – measure with `--estimate-only --grad-checkpointing` |
| `--estimate-only` | – | measures the batch size and the speed, then stops before training |
| `--gpu 1` | all cards | use a single GPU while the other one is busy |
| `--resume` | – | continue from the last checkpoint |

Two GPUs: on Linux `torchrun --nproc_per_node 2 training/train_mt5.py …` (NCCL, fastest and most memory-efficient).
Windows has no NCCL – start without `torchrun` and the Trainer gives each card its own batch through
`torch.nn.DataParallel`. Because DataParallel gathers the outputs of all cards on card 0 (several GiB with a 250,000
piece vocabulary), the probe keeps 2 GiB extra free there. bf16 is used automatically; fp16 is unstable with mT5.
Check `nvidia-smi` first: at 1024 tokens, batch 6 including optimizer state and allocator fragmentation takes about
13–14 GiB per card – **plan for ~14 GiB free per card**, and other models fill 15 GB quickly. Without
`--grad-checkpointing` the probe only picks batch 2 on a 16 GB card; with checkpointing it picks batch 6 and is faster
overall.

Result: `runs/…/final/` (model, tokenizer, `metadata_generation.json` with prefixes and lengths),
`runs/…/samples_test.jsonl` (generated and reference metadata for test materials) and `runs/…/tokenized/` (cached
tokenization, reused when the settings are unchanged).

## 4. Evaluate and compare

```
python training/evaluate_meta.py --model runs/mt5-meta/final --data training/hf_export --n 200 --out runs/mt5-meta/eval_test.jsonl
python training/evaluate_meta.py --model runs/pilot/final runs/mt5-meta/final --n 200
```

Title: exact matches and word-overlap F1 · description: ROUGE-L F1 · keywords: F1 over the keyword lists. Rough bar for
a usable model: title exact ≳ 0.25 and word F1 ≳ 0.6, keyword F1 ≳ 0.5, ROUGE-L ≳ 0.3. Inference takes roughly 13 s
per material, so the script prints progress with elapsed time and an ETA (a live line in a terminal, a line every
10 % when the output is redirected).

## 5. Use

```
python training/predict.py --model runs/mt5-meta/final text.txt
python training/predict.py --model runs/mt5-meta/final --num-beams 1 text.txt   # faster
python training/predict.py --model runs/mt5-meta/final --device cuda text.txt    # on the GPU (bf16)
python training/demo_ui.py --model runs/mt5-meta/final --device cuda             # test window
```

`demo_ui.py` opens a small window: five sample texts from the test split can be loaded with one click (their reference
metadata is shown below the result), or paste any text. Tick the fields you want – one, two or all three – and press
*Erzeugen*; the *nur …* buttons generate a single field directly. The number of beams is adjustable, and the window
shows tokens, generation time per field and whether the input gets cut. Generation runs in a background thread, so the
window stays responsive.

Clean real web pages first (navigation, cookie notices, footers). `predict.py` reads the length from
`metadata_generation.json` and cuts the input at 1024 tokens exactly as training does – for long web page extracts
that is the same cut the model saw in 15 % of the training materials. It prints how many tokens the input has and
whether it was cut. For even longer texts, compare `head` and `condensed` – the preprocessing in `textprep.py` is the
same as in training.

## 6. Publish to the Hugging Face Hub

```
hf auth login
python training/upload_model.py --repo JanSchachtschabel/mt5-small-german-educational-metadata
```

The model card in `training/model_card.md` becomes the README of the Hub repository; edit it there, not on the Hub.
`upload_model.py` checks the required files first, creates the repository if needed and uploads `runs/mt5-meta/final`
without `training_args.bin`. `--dry-run` prints what would be uploaded, `--private` creates a private repository.
