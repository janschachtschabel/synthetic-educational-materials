# mT5-Finetuning: Volltext → Titel, Beschreibung, Keywords

*[English](README.md) · Deutsch*

Musterskripte, um aus den generierten Materialien einen Hugging-Face-Datensatz zu bauen und ein kleines
Text-zu-Text-Modell (Standard: [`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1))
für Metadaten aus Volltexten zu trainieren. Das trainierte Modell läuft danach auch auf der CPU.

Gemessene Zahlen zu Datensatz, Tokenlängen, Rechenzeit und Hardware: **[TRAININGSWISSEN.md](TRAININGSWISSEN.md)**.

| Datei | Zweck |
|---|---|
| `export_hf.py` | Materialien-JSONL → flacher Datensatz (Parquet, Splits nach Thema) mit englischer Datensatzkarte |
| `dataset_card.md` | Vorlage der Datensatzkarte; Zahlen setzt `export_hf.py` ein |
| `train_mt5.py` | Finetuning mit Source-Prefixen `title: `, `description: `, `keywords: ` |
| `evaluate_meta.py` | Modelle auf Testmaterialien vergleichen: Titel-Treffer, Keyword-F1, ROUGE-L |
| `predict.py` | Metadaten für eine Textdatei erzeugen, mit Zeit je Feld |
| `demo_ui.py` | kleines Testfenster: Text einfügen oder eines von 5 Mustern laden, einzelnes Feld oder alle drei erzeugen |
| `upload_model.py` | finales Modell und Modellkarte auf den Hugging Face Hub laden |
| `textprep.py` | gemeinsame Vorverarbeitung der Eingabe (Training und Nutzung gleich) |

## 1. Installieren

PyTorch passend zur CUDA-Version der Grafikkarte installieren. Für die RTX-50-Reihe (Compute Capability 12.0) sind
CUDA-12.8-Räder nötig, ältere Builds kennen die Karte nicht.

```
uv venv --python 3.13 .venv-train
uv pip install --python .venv-train\Scripts\python.exe "torch>=2.7" --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv-train\Scripts\python.exe -r training\requirements.txt
```

Ohne `uv` dasselbe mit `python -m venv .venv-train` und dem `pip` dieser Umgebung.

## 2. Datensatz holen

Eigene Materialien exportieren und hochladen:

```
python training/export_hf.py output/materialien_….jsonl --out training/hf_export
huggingface-cli login
huggingface-cli upload JanSchachtschabel/synthetic-german-educational-materials-50k training/hf_export . --repo-type dataset
```

oder den fertigen Datensatz holen (Parquet, 149 MB, ohne Login):

```
huggingface-cli download JanSchachtschabel/synthetic-german-educational-materials-50k --repo-type dataset --local-dir training/hf_export
```

Exportiert werden nur die für das Training nützlichen Felder, flach und mit englischen Spaltennamen. Herkunftsangaben,
Generierungsdetails und Themenlabels aus nur lokal gehaltenen Quellen bleiben draußen. Train/Validation/Test sind nach
Thema getrennt (90/5/5), damit Testthemen im Training nicht vorkommen: 45.144 / 2.463 / 2.393 Materialien.

## 3. Trainieren

```
# Längen ansehen: wie viel Volltext in 512 / 768 / 1024 / 1280 Tokens passt (trainiert nicht)
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --report-only

# nur messen: welche Batchgröße auf die Karte passt und wie lang der Lauf dauert (ohne Training)
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --estimate-only

# Pilot: 5.000 Materialien, 1 Epoche
python training/train_mt5.py --data training/hf_export --out runs/pilot --max-train 5000 --epochs 1

# voller Lauf: 45.144 Materialien × 3 Aufgaben, 3 Epochen
python training/train_mt5.py --data training/hf_export --out runs/mt5-meta --epochs 3
```

`--data` ist der Ordner von `export_hf.py` (auch der heruntergeladene) oder eine Hub-ID. Je Material und Aufgabe entsteht
ein Beispiel, alle werden gemischt. Vor dem Training stehen die Tokenlängen, danach die von der Grafikkarte verpackte
Batch-Größe und die **geschätzte Gesamtdauer** (über 30 echte Schritte gemessen) – abbrechen und mit `--input-mode
condensed --max-source-length 512` neu starten, wenn es zu lange dauert.

Wichtige Optionen:

| Option | Standard | Hinweis |
|---|---|---|
| `--max-source-length` | 1024 | Volltexte brauchen im Median 922 Tokens (mit Prefix); in 768 passt nur 1,4 %, in 1024 passen 84,7 % ganz |
| `--input-mode` | `head` | `condensed`: Überschriften + erste Sätze je Abschnitt, median 145 Tokens, etwa halbe Rechenzeit |
| `--max-target-title/-description/-keywords` | 48 / 256 / 96 | deckt 99 % der Felder ab |
| `--epochs`, `--lr` | 3, 1e-4 | nach 2 Epochen flacht der Validierungsverlust ab |
| Batch je Karte | automatisch | Sonde misst mehrere Schritte mit variierenden Längen **inklusive Optimizer-Zustand** und hält 1,5 GiB Abstand zum Limit (2 Karten: 3,5 GiB); `--batch-size N` setzt fest |
| `--grad-checkpointing` | – | ~30 % langsamer pro Schritt, aber viel weniger Aktivierungs-Speicher; auf 16-GB-Karten oft der Unterschied zwischen Batch 2 und Batch 6 – mit `--estimate-only --grad-checkpointing` nachmessen |
| `--estimate-only` | – | misst Batchgröße und Tempo und bricht ab, bevor trainiert wird |
| `--gpu 1` | alle Karten | nur eine Grafikkarte nutzen, solange die andere belegt ist |
| `--resume` | – | dort weitermachen, wo der letzte Checkpoint stand |

Zwei Grafikkarten: unter Linux `torchrun --nproc_per_node 2 training/train_mt5.py …` (NCCL, am schnellsten und am
sparsamsten im Speicher). Unter Windows gibt es kein NCCL – ohne `torchrun` starten, der Trainer verteilt dann mit
`torch.nn.DataParallel` pro Karte eine Batchgröße. Weil DataParallel die Ausgaben aller Karten auf Karte 0 sammelt (bei
250.000 Wörterbuchstücken mehrere GiB), hält die Sonde dort zusätzlich 2 GiB frei. bf16 wird automatisch genutzt, fp16
ist bei mT5 instabil. Vorher `nvidia-smi` prüfen: mit 1024 Tokens braucht Batch 6 inklusive Optimizer-Zustand und
Allocator-Streuverlust etwa 13–14 GiB pro Karte – **pro Karte also ~14 GiB frei** einplanen; andere Modelle belegen so
schnell 15 GB. Ohne `--grad-checkpointing` wählt die Sonde auf einer 16-GB-Karte nur Batch 2, mit Checkpointing Batch 6
und ist unter dem Strich schneller.

Ergebnis: `runs/…/final/` (Modell, Tokenizer, `metadata_generation.json` mit Prefixen und Längen),
`runs/…/samples_test.jsonl` (Testmaterialien mit erzeugten und Referenz-Metadaten) und `runs/…/tokenized/`
(Zwischenspeicher der Tokenisierung, wird bei gleichen Einstellungen wiederverwendet).

## 4. Auswerten und vergleichen

```
python training/evaluate_meta.py --model runs/mt5-meta/final --data training/hf_export --n 200 --out runs/mt5-meta/eval_test.jsonl
python training/evaluate_meta.py --model runs/pilot/final runs/mt5-meta/final --n 200
```

Titel: exakte Treffer und Wortüberlappungs-F1 · Beschreibung: ROUGE-L F1 · Keywords: F1 auf den Schlagwortlisten.
Richtwerte für ein brauchbares Modell: Titel exakt ≳ 0,25 und Wort-F1 ≳ 0,6, Keyword-F1 ≳ 0,5, ROUGE-L ≳ 0,3.
Die Inferenz dauert ca. 13 s pro Material; das Skript zeigt daher den Fortschritt mit Laufzeit und ETA (im Terminal
eine live aktualisierte Zeile, bei umgeleiteter Ausgabe eine Zeile alle 10 %).

## 5. Nutzen

```
python training/predict.py --model runs/mt5-meta/final text.txt
python training/predict.py --model runs/mt5-meta/final --num-beams 1 text.txt   # schneller
python training/predict.py --model runs/mt5-meta/final --device cuda text.txt    # auf der GPU (bf16)
python training/demo_ui.py --model runs/mt5-meta/final --device cuda             # Testfenster
```

`demo_ui.py` öffnet ein kleines Fenster: fünf Mustertexte aus dem Test-Split lassen sich per Klick laden (die
Referenzmetadaten stehen unter dem Ergebnis), oder du fügst einen beliebigen Text ein. Felder ankreuzen – eines, zwei
oder alle drei – und *Erzeugen* drücken; die Knöpfe *nur …* erzeugen direkt ein einzelnes Feld. Die Beam-Zahl ist
einstellbar, und das Fenster zeigt Tokens, Zeit je Feld und ob die Eingabe gekürzt wird. Die Erzeugung läuft in einem
Hintergrund-Thread, das Fenster bleibt also bedienbar.

Echte Webseiten vorher säubern (Navigation, Cookie-Hinweise, Fußzeilen). `predict.py` liest die Länge aus
`metadata_generation.json` und kürzt den Input wie im Training auf 1024 Tokens – bei langen Volltext-Extrakten von
Webseiten also derselbe Schnitt, den das Modell aus 15 % der Trainingsmaterialien kennt. Es druckt, wie viele Tokens
der Input hat und ob er gekürzt wurde. Für noch längere Texte lohnt ein Vergleich von `head` und `condensed` – die
Vorverarbeitung in `textprep.py` ist identisch mit der im Training.

## 6. Auf den Hugging Face Hub laden

```
hf auth login
python training/upload_model.py --repo JanSchachtschabel/mt5-small-german-educational-metadata
```

Die Modellkarte in `training/model_card.md` wird die README des Hub-Repositories; Änderungen also dort machen, nicht
auf dem Hub. `upload_model.py` prüft zuerst die nötigen Dateien, legt das Repository bei Bedarf an und lädt
`runs/mt5-meta/final` ohne `training_args.bin` hoch. `--dry-run` zeigt nur, was hochgeladen würde, `--private` legt
ein privates Repository an.
