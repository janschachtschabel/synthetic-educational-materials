# Knowledge: full text → title, description, keywords (mT5 fine-tuning)

*English · [Deutsch](TRAININGSWISSEN.md)*

What is known about training a small text-to-text model on the generated materials. The numbers below were measured on
the dataset `JanSchachtschabel/synthetic-german-educational-materials-50k` (2026-11-04), not estimated.

## 1. Task

Input: the full text of an educational material (Markdown, about one A4 page). Output: three metadata fields that the
dataset itself derived **from the full text** (the generating model never saw the seed's topic or keywords):

| Task | prefix in the input | target field |
|---|---|---|
| title | `title: ` | `title` |
| description | `description: ` | `description` |
| keywords | `keywords: ` | `keywords` (list, joined with commas) |

One example per material and task, all shuffled together (`training/textprep.py` owns the prefixes; training and
inference use the same preparation). 45,144 training materials become **135,432 examples per epoch**.

## 2. Dataset in numbers

Splits are separated **by topic** (hash of the topic URI), so test topics never appear during training.

| Split | materials |
|---|---|
| train | 45,144 |
| validation | 2,463 |
| test | 2,393 |

Characters (median / p95 / max): full text 3,663 / 4,528 / 6,914 · title 67 / 85 / 116 · description 782 / 922 / 1,129 ·
keywords 5–8 per material, median 132 characters as text. Fields used: `id`, `fulltext`, `title`, `description`,
`keywords`. Everything else (`qa_pairs`, subjects, content type, level, …) is not needed for this task.

## 3. Token lengths with the mT5 tokenizer (this decides the length limit)

mT5 has 250,100 word pieces, so German full texts are longer than the usual 512 tokens. The table counts the task
prefix (3 tokens) and the end token, exactly as training does; `train_mt5.py --report-only` prints the same numbers.

| input mode | median | p90 | p95 | max | fits in 512 | fits in 768 | fits in 1024 | fits in 1280 |
|---|---|---|---|---|---|---|---|---|
| `head` (plain text, from the start) | 922 | 1,078 | 1,164 | 1,525 | 0 % | **1.4 %** | 84.7 % | 98.2 % |
| `condensed` (headings + first two sentences per section) | 145 | 344 | 396 | 674 | 99.5 % | 100 % | 100 % | 100 % |

So **`--max-source-length 768` was too small** – almost every text would have been cut. The default is `head` with
**1024**: all 45,144 materials are trained, but for 15 % the ending of the text is missing. 1280 was deliberately not
chosen (98.2 % coverage) because compute grows almost linearly with the input length – 1280 costs about 25 % per
example. `condensed` is the fast variant: half the compute, the middle sentences are gone but the structure of the
whole text stays visible.

**The cut is intended, not just a compromise.** In use the model sits behind full-text extracts from web pages, which
are longer than the window and are cut at 1024 tokens as well. A model that only ever saw complete texts would meet an
input it never trained on; a model that saw truncated inputs with complete references for 15 % of the data learned the
behaviour “the beginning is enough for a title, keywords and a description”. Truncation removes the **end** of the
text, not the middle – `prepare_input` keeps the start, exactly as later use does.

Target fields in tokens (median / p95 / max): title 15 / 20 / 30 · description 188 / 220 / 259 · keywords 35 / 48 / 64.
The script's limits (48 / 256 / 96) therefore cut almost nothing; 256 tokens cover about 99 % of the descriptions.

## 4. Base model

[`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1), MIT
licensed, 300 M parameters: mT5-small (d_model 512, 8 encoder + 8 decoder layers, d_ff 1024, 6 heads) additionally
fine-tuned on Swiss texts for German summarization. Properties that matter here:

- `relative_attention_num_buckets: 32`, no fixed position count – lengths up to ~1,500 tokens run, but quality beyond
  ~512 tends to drop because mT5 itself was trained with shorter windows.
- `tie_word_embeddings: false` – input embedding and output layer are separate (~128 M parameters each), which makes the
  decoder a large share of the compute time.
- **fp16 is unstable with mT5** (overflow in the attention softmaxes); train in **bf16** (the RTX 5060 Ti supports it) or
  fp32. The script picks bf16 automatically, fp32 otherwise.

## 5. Compute time and hardware

Order of magnitude per example (1,024 input + ~200 output tokens): forward and backward pass around 3·10¹¹ FLOPs. On one
RTX 5060 Ti (bf16, narrow layers → launch-bound) roughly **1.5–6 h per epoch**, about half of that with two cards.
`train_mt5.py` measures this after ~20 real steps and prints the total time before the actual training starts – abort
and switch to `condensed` if it is too slow.

Machine: 2 × RTX 5060 Ti with 16 GB (compute capability 12.0 / Blackwell). That requires **PyTorch built for CUDA 12.8
or newer** (`cu128` wheels); older CUDA builds do not know sm_120. Prepared environment: `.venv-train` with
`torch 2.11.0+cu128`, `transformers 5.22.1`, `datasets 5.1.0`, `accelerate 1.15.0`.

Memory while training (bf16 autocast, AdamW with fp32 states): about 6 GB fixed (weights, gradients, optimizer) plus
activations – with batch 16 at 1,024 tokens roughly 10–12 GB. Other programs (LM Studio, models that keep their
weights in VRAM) quickly occupy 15 GB per card, and then training does not fit anymore. Use `--gpu 1` for a single card
and check `nvidia-smi` before starting.

Two cards on **Windows**: no NCCL, so no real DDP. The script leaves both cards visible and the Trainer then splits the
batch with `torch.nn.DataParallel` (GPU 0 additionally holds the gathered outputs – halve the batch on OOM). On Linux
`torchrun --nproc_per_node 2 …` is faster. `torchrun` on Windows only works with gloo (set automatically) and is
usually slower than DataParallel.

## 6. Recommended settings

| setting | value | reason |
|---|---|---|
| epochs | 3 | validation loss flattens after two epochs; `load_best_model_at_end` keeps the best |
| learning rate | 1e-4, linear decay, 3 % warmup | standard for mT5-small fine-tuning; smaller is too slow for 135k examples |
| batch per GPU | automatic, ~6–8 | the probe walks 1…48 with the **longest** examples and keeps 1.5 GiB free (3.5 GiB with two GPUs) |
| gradient accumulation | 1 | not needed once the batch is large |
| sampling | `group_by_length` (one card) / `batch_rebalance` (two cards) | less padding, steadier utilisation |
| validation | 1,000 materials per epoch | eval_loss decides which checkpoint is best |

Measured on this machine (bf16, 1024 input / 256 output tokens, both cards free): the probe picks **batch 6 per card** –
batch 8 needs 9.6 GiB and just misses the 9.2 GiB budget (12.7 GiB free minus the 3.5 GiB distance). Speed at that
batch is **12 examples/s per card**, i.e. ~3.1 h per epoch: **9.0 h for three epochs on one card, ~5.3 h with two
cards**. `--estimate-steps` re-measures this, and `--estimate-only` only measures and stops. With
`--grad-checkpointing` a larger batch fits into the same memory; whether that is faster overall, measure it before the
real run with `--estimate-only --grad-checkpointing`.

### The embedding trap (transformers 5)

Setting `config.tie_word_embeddings = False` **before** loading is wrong here: the base model stores the shared
embedding once as `shared.weight`, but with `tie=False` the loader expects `encoder.embed_tokens.weight` /
`decoder.embed_tokens.weight`, does not find them and **initializes them at random** – the model then grows from
300.2 M to 556.3 M parameters and trains from scratch. The correct order: load normally, set the flag **after** loading
(so the saved config tells the truth – saving and reloading was checked: identical weights, no missing keys). The script
also checks `missing_keys` after loading and aborts instead of training a broken model.

Two messages at load time look alike and mean different things – both quoted verbatim, because they were already
confused once:

| message while loading | meaning |
|---|---|
| `The tied weights mapping … but both are present in the checkpoints with different values, so we will NOT tie them` | **harmless.** Both layers are stored separately in the checkpoint and are taken over; this also appeared in the first, working run. |
| `MISSING … newly initialized`, missing keys `encoder/decoder.embed_tokens.weight` | **broken.** The input embedding comes from the random number generator; the model has 556 M parameters instead of 300 M. |

The loss of the first training step makes it visible in one number: loaded correctly this base model starts between 3
and 7 (depending on task and input length), with a random embedding it starts at 13.9 – pure chance would be 12.4.
`train_mt5.py` prints that value before training and warns from 11 upward.

### Why the first attempt ran out of memory

Three things came together – all three are handled in the script now:

1. **Batch sizes are per card.** The Trainer multiplies `per_device_train_batch_size` by the number of cards itself
   (`train_batch_size = per_device × n_gpu`) and DataParallel splits that again. Multiplying the probe value by the
   card count as well trains with double the batch per card → OOM.
2. **The probe must not try average texts.** `group_by_length` puts the longest examples into the first batches, so the
   probe uses the longest 64 examples and checks the GiB actually reserved instead of only “did it run through”.
3. **The output layer is the biggest block.** 250,100 vocabulary pieces × up to 256 target tokens: the logits are ~1 GiB
   under bf16 autocast and the cross entropy recomputes them in fp32 for another ~2 GiB. With two cards the outputs
   gathered from the second card land on card 0 on top – hence the 3.5 GiB safety distance there. DataParallel is
   memory-hungry for a vocabulary that large; one process per card (`torchrun`, Linux) is cheaper.

`--max-train 5000 --epochs 1` is the pilot (under an hour); afterwards the same command without `--max-train`.
Repeated runs with identical settings do not tokenize again (`runs/…/tokenized/<hash>`).

## 7. Evaluation

`training/evaluate_meta.py` generates the three fields for test materials and reports

- title: exact-match rate and word-overlap F1,
- description: ROUGE-L F1,
- keywords: F1 over the comma-separated keyword strings.

Compare two models with `--model runs/pilot/final runs/mt5-meta/final --n 200`. Rough bar for a healthy model on this
dataset: title exact ≳ 0.25 and word F1 ≳ 0.6, keyword F1 ≳ 0.5, ROUGE-L ≳ 0.3 – clearly below that, read
`samples_*.jsonl` (repeated phrases, empty outputs, cut sentences). eval_loss alone is only comparable between runs with
the same task mix.

Results of this run: *(fill in after training)*

| model | eval_loss | title exact/F1 | keyword F1 | ROUGE-L |
|---|---|---|---|---|
| pilot (5k, 1 epoch) | | | | |
| full (45k, 3 epochs) | | | | |

## 8. Known pitfalls

- **Truncation**: with `head`/1024, 15 % of the texts lose their ending, which makes their descriptions harder to
  learn. `condensed` removes the length problem but drops detail.
- **transformers 5**: `group_by_length` and `pad_to_multiple_of` are no longer training arguments
  (`train_sampling_strategy`, padding through the data collator). The script handles both versions.
- **Tokenization**: 135k examples take about 10 minutes (SentencePiece, four processes); the result is cached.
- **Memory layout**: the script sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, because the varying text
  lengths otherwise leave fragmented blocks behind.
- **Windows**: `dataloader_num_workers` defaults to 0 because spawning processes is expensive; 4 is faster on Linux.
- **Real texts** are longer and noisier (navigation, cookie notices) than the synthetic materials – clean them before
  use; the 1024-token cut happens in `predict.py` exactly as it does in training.
- The materials come from a single LLM and one prompt family; the model learns that style.
