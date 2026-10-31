# mT5-Finetuning: Volltext → Titel, Beschreibung, Keywords

*[English](README.md) · Deutsch*

Musterskripte, um aus den generierten Materialien einen Hugging-Face-Datensatz zu bauen und ein kleines
Text-zu-Text-Modell (Standard: [`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1))
für Metadaten aus Volltexten zu trainieren. Das trainierte Modell läuft danach auf der CPU.

| Datei | Zweck |
|---|---|
| `export_hf.py` | Materialien-JSONL → flacher Datensatz (Parquet, Splits nach Thema) mit englischer Datensatzkarte |
| `dataset_card.md` | Vorlage der Datensatzkarte; Zahlen setzt `export_hf.py` ein |
| `train_mt5.py` | Finetuning mit Source-Prefixen `title: `, `description: `, `keywords: ` |
| `predict.py` | Metadaten für eine Textdatei erzeugen, mit Zeit je Feld |
| `textprep.py` | gemeinsame Vorverarbeitung der Eingabe (Training und Nutzung gleich) |

## 1. Installieren

```
pip install torch --index-url https://download.pytorch.org/whl/cu124   # passend zur CUDA-Version
pip install -r training/requirements.txt
```

## 2. Datensatz exportieren und hochladen

```
python training/export_hf.py output/materialien_….jsonl --out hf_export
huggingface-cli login
huggingface-cli upload JanSchachtschabel/synthetic-german-educational-materials-50k hf_export . --repo-type dataset
```

Exportiert werden nur die für Training nützlichen Felder, flach und mit englischen Spaltennamen. Herkunftsangaben,
Generierungsdetails und Themenlabels aus nur lokal gehaltenen Quellen (Lernfelder, Bildungsstandards,
Rahmencurricula) bleiben draußen. Train/Validation/Test werden nach Thema getrennt (90/5/5), damit Testthemen im
Training nicht vorkommen. Vor dem Hochladen `hf_export/README.md` lesen; die Lizenz steht dort als CC BY 4.0.

## 3. Trainieren

```
# Pilot: 5.000 Materialien, 1 Epoche
python training/train_mt5.py --data hf_export --out runs/pilot --max-train 5000 --epochs 1

# voll, zwei GPUs (Linux)
torchrun --nproc_per_node 2 training/train_mt5.py --data hf_export --out runs/mt5-meta
```

Statt `hf_export` geht auch die Hub-ID des hochgeladenen Datensatzes. Das Skript erzeugt je Material und Aufgabe ein
Beispiel und mischt alle zufällig. Vor dem Training zeigt es die Tokenlängen von Eingabe und Zielen und wie viel bei
den gewählten Grenzen abgeschnitten wird.

Wichtige Optionen:

| Option | Standard | Hinweis |
|---|---|---|
| `--max-source-length` | 768 | Basismodell kennt bis 800; 512 ist auf der CPU schneller |
| `--max-target-title/-description/-keywords` | 48 / 256 / 96 | Beschreibungen haben im Median ca. 800 Zeichen, mehr als die 96 Tokens des Basismodells |
| `--input-mode` | `head` | `condensed`: Überschriften + erste Sätze je Abschnitt, für längere echte Texte |
| `--epochs`, `--lr` | 3, 1e-4 | bei 150.000 Beispielen reichen 2–3 Epochen |
| `--batch-size`, `--grad-accum` | 8, 2 | je GPU |

Auf GPUs mit bf16 (ab Ampere) wird in bf16 trainiert, sonst in fp32; fp16 ist bei mT5 instabil. Unter Windows läuft
`torchrun` mit zwei GPUs meist nicht (kein NCCL); dort eine GPU nutzen oder WSL2.

Ergebnis: `runs/…/final/` (Modell, Tokenizer, `metadata_generation.json` mit Prefixen und Längen) und
`runs/…/samples_test.jsonl` mit erzeugten und Referenz-Metadaten für 50 Testmaterialien.

## 4. Nutzen

```
python training/predict.py --model runs/mt5-meta/final text.txt
python training/predict.py --model runs/mt5-meta/final --num-beams 1 text.txt   # schneller
```

Echte Webseiten vorher säubern (Navigation, Cookie-Hinweise, Fußzeilen). Die Trainingstexte sind etwa eine A4-Seite
lang; bei längeren Texten lohnt ein Vergleich von `head` und `condensed`.
