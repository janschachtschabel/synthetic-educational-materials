# Wissen: Volltext → Titel, Beschreibung, Keywords (mT5-Feintraining)

*[English](TRAINING_NOTES.md) · Deutsch*

Wissensstand zum Training eines kleinen Text-zu-Text-Modells auf den generierten Materialien. Zahlen unten sind am
Datensatz `JanSchachtschabel/synthetic-german-educational-materials-50k` gemessen (Stand 2026-11-04), nicht geschätzt.

## 1. Aufgabe

Eingang: der Volltext eines Bildungsmaterials (Markdown, etwa eine A4-Seite). Ausgang: drei Metadatenfelder, die im
Datensatz **aus dem Volltext heraus** generiert wurden (das Generierungsmodell hat dabei weder Thema noch Keywords des
Seeds gesehen):

| Aufgabe | Prefix im Eingabetext | Zielfeld im Datensatz |
|---|---|---|
| Titel | `title: ` | `title` |
| Beschreibungstext | `description: ` | `description` |
| Keywords | `keywords: ` | `keywords` (Liste, als Komma-Text) |

Ein Beispiel je Material und Aufgabe, alle gemischt (`training/textprep.py` setzt die Prefixe, Training und Nutzung
verwenden dieselbe Vorverarbeitung). Aus 45.144 Trainingsmaterialien werden so **135.432 Beispiele pro Epoche**.

## 2. Datensatz in Zahlen

Splits sind **nach Thema** getrennt (Hash der Themen-URI), Testthemen kommen im Training nicht vor.

| Split | Materialien |
|---|---|
| train | 45.144 |
| validation | 2.463 |
| test | 2.393 |

Zeichen (Median / p95 / max): Volltext 3.663 / 4.528 / 6.914 · Titel 67 / 85 / 116 · Beschreibung 782 / 922 / 1.129 ·
Keywords 5–8 Stück, als Text median 132 Zeichen. Genutzte Felder: `id`, `fulltext`, `title`, `description`,
`keywords`. Die restlichen Felder (`qa_pairs`, Fächer, Inhaltstyp, Niveau, …) werden für diese Aufgabe nicht gebraucht.

## 3. Tokenlängen mit dem mT5-Tokenizers (entscheidend für die Länge)

mT5 hat ein Vokabular von 250.100 Stücken; deutsche Volltexte sind damit länger als die oft üblichen 512 Tokens.
Die Tabelle zählt – wie das Training – das Aufgaben-Prefix (3 Tokens) und das Abschluss-Token mit; dieselben Zahlen
prints `train_mt5.py --report-only`.

| Eingabemodus | Median | p90 | p95 | max | passt in 512 | passt in 768 | passt in 1024 | passt in 1280 |
|---|---|---|---|---|---|---|---|---|
| `head` (Volltext, Textanfang) | 922 | 1.078 | 1.164 | 1.525 | 0 % | **1,4 %** | 84,7 % | 98,2 % |
| `condensed` (Überschriften + erste 2 Sätze je Abschnitt) | 145 | 344 | 396 | 674 | 99,5 % | 100 % | 100 % | 100 % |

Daraus: **`--max-source-length 768` war zu klein** – fast jeder Text wäre abgeschnitten worden. Standard ist `head` mit
**1024**: alle 45.144 Materialien werden trainiert, bei 15 % fehlt allerdings das Textende. Bewusst nicht auf 1280
gegangen (98,2 % Abdeckung), weil die Rechenzeit mit der Eingabelänge fast linear steigt – 1280 kostet rund 25 % mehr
pro Beispiel. `condensed` ist die schnelle Variante: halbe Rechenzeit, dafür fehlen die mittleren Sätze, die Struktur
des ganzen Textes bleibt erhalten.

**Der Schnitt ist gewollt, nicht nur ein Kompromiss.** Im Einsatz hängen die Modelle an Volltext-Extrakten von
Webseiten, die länger sind als das Fenster und ebenfalls bei 1024 Tokens enden. Ein Modell, das nur vollständige Texte
gelernt hat, sähe dort eine Eingabe, die es nicht kennt; eines, das zu 15 % gekürzte Texte mit vollständigen
Referenzen sieht, hat das Verhalten „Anfang reicht für Titel, Keywords und eine Beschreibung" mitgelernt. Beim
Ausschneiden geht der **Textschluss** verloren, nicht die Mitte – `prepare_input` schneidet am Anfang, wie später die
Nutzung.

Zielfelder in Tokens (Median / p95 / max): Titel 15 / 20 / 30 · Beschreibung 188 / 220 / 259 · Keywords 35 / 48 / 64.
Die Grenzen des Skripts (48 / 256 / 96) schneiden damit praktisch nichts ab; 256 Tokens für Beschreibungen reichen für
rund 99 % der Materialien.

## 4. Basismodell

[`deutsche-telekom/mt5-small-sum-de-mit-v1`](https://huggingface.co/deutsche-telekom/mt5-small-sum-de-mit-v1),
lizenziniert MIT, 300 Mio. Parameter: mT5-small (d_model 512, 8 Encoder- + 8 Decoder-Layern, d_ff 1024, 6 Köpfe),
zusätzlich auf Schweizer Texten für deutsche Zusammenfassung feintrainiert. Relevante Eigenschaften:

- `relative_attention_num_buckets: 32`, keine feste Positionsanzahl – Längen bis ~1.500 Tokens laufen, Qualität jenseits
  von ~512 sinkt aber tendenziell, weil mT5 mit kürzeren Fenstern trainiert wurde.
- `tie_word_embeddings: false` – Einbettung und Ausgabeschicht sind getrennt (je ~128 Mio. Parameter), das macht den
  Decoder-Anteil der Rechenzeit bedeutend.
- **fp16 ist bei mT5 instabil** (Overflow in den Attention-Softmaxes), training in **bf16** (RTX 5060 Ti kann bf16) oder
  fp32. Das Skript wählt bf16 automatisch, sonst fp32.

## 5. Rechenzeit und Hardware

Gemessene Hausnummer pro Beispiel (1.024 Eingabe- + ~200 Ausgabetokens): Vorwärts- und Rückwärtsdurchlauf
größenordnung ~3·10¹¹ FLOPs. Auf einer RTX 5060 Ti (bf16, kleine Schichten → launch-limitiert) realistisch etwa
**1,5–6 h pro Epoche**, mit zwei Karten etwa die Hälfte. `train_mt5.py` misst das nach ~20 echten Schritten und druckt
die Gesamtdauer, bevor das eigentliche Training startet – abbrechen und auf `condensed` wechseln, wenn es zu lange ist.

Gerät: 2 × RTX 5060 Ti mit 16 GB (Compute Capability 12.0 / Blackwell). Dazu nötig: **PyTorch mit CUDA 12.8 oder neuer**
(`cu128`-Räder), ältere CUDA-Builds kennen sm_120 nicht. eingecheckte Umgebung: `.venv-train` mit
`torch 2.11.0+cu128`, `transformers 5.22.1`, `datasets 5.1.0`, `accelerate 1.15.0`.

Speicherbedarf beim Training (bf16-Autocast, AdamW mit fp32-Zuständen): rund 6 GB Grundverbrauch (Gewichte, Gradienten,
Optimizer) plus Aktivierungen – mit Batch 16 bei 1.024 Tokens grob 10–12 GB. Andere Programme (LM Studio, inferende
Modelle) belegen schnell 15 GB pro Karte; dann passt kein Training mehr. Deshalb:
`--gpu 1` nutzt nur eine Karte, `nvidia-smi` vor dem Start prüfen.

Zwei Karten unter **Windows**: kein NCCL, also kein echtes DDP. Das Skript lässt beide Karten sichtbar, der Trainer
verteilt den Batch dann mit `torch.nn.DataParallel` (Karte 0 trägt zusätzlich die zusammengeführten Ausgaben – bei
OOM Batch halbieren). Unter Linux ist `torchrun --nproc_per_node 2 …` schneller. `torchrun` unter Windows funktioniert
nur mit gloo (`--ddp-*` wird automatisch gesetzt) und ist meist langsamer als DataParallel.

## 6. Empfohlene Einstellungen

| Einstellung | Wert | Begründung |
|---|---|---|
| Epochen | 3 | nach 2 Epochen flacht der Validierungsverlust meist ab; `load_best_model_at_end` holt das beste |
| Lernrate | 1e-4, linear, 3 % Warmup | Standard für mT5-small-Finetuning; kleiner wäre zu träge bei 135k Beispielen |
| Batch je Karte | automatisch, ca. 6–8 | die Sonde probiert 1…48 mit den **längsten** Beispielen und hält 1,5 GiB frei (3,5 GiB bei zwei Karten) |
| Gradient Accumulation | 1 | bei großer automatisch gewählter Batchgröße nicht nötig |
| Sampling | `group_by_length` (1 Karte) bzw. `batch_rebalance` (2 Karten) | wenig Padding, gleichmäßigere Auslastung |
| Validierung | 1.000 Materialien je Epoche | eval_loss entscheidet über das beste Modell |

Gemessen auf dieser Maschine (bf16, 1024 Eingabe- / 256 Ausgabetokens, beide Karten frei): die Sonde wählt **Batch 6
pro Karte** – Batch 8 braucht 9,6 GiB und fällt damit knapp aus dem Budget von 9,2 GiB (12,7 GiB frei abzüglich
3,5 GiB Abstand). Tempo dabei **12 Beispiele/s pro Karte**, also ~3,1 h pro Epoche: **9,0 h für drei Epochen auf einer
Karte, ~5,3 h mit zwei Karten**. `--estimate-steps` misst das neu, `--estimate-only` macht nichts anderes als messen
und aufhören. Mit `--grad-checkpointing` passt ein größeres Batch in denselben Speicher; ob das unter dem Strich
schneller ist, vor dem echten Lauf mit `--estimate-only --grad-checkpointing` messen.

### Die Einbettungs-Falle (transformers 5)

`config.tie_word_embeddings = False` **vor** dem Laden zu setzen ist hier falsch: das Basismodell speichert die
gemeinsame Einbettung einmal als `shared.weight`, mit `tie=False` erwartet der Lader aber
`encoder.embed_tokens.weight` / `decoder.embed_tokens.weight`, findet sie nicht und **initialisiert sie zufällig** –
das Modell wächst dann von 300,2 Mio. auf 556,3 Mio. Parameter und trainiert von Grund auf. Richtig ist: normal laden,
den Schalter **nach** dem Laden auf `False` setzen (damit die gespeicherte Konfiguration die Wahrheit sagt – Speichern
und erneutes Laden wurde geprüft: Gewichte identisch, keine fehlenden Schlüssel). Das Skript prüft nach dem Laden
zusätzlich `missing_keys` und bricht ab, statt ein kaputtes Modell zu trainieren.

Zwei Meldungen beim Laden ähneln sich und bedeuten Verschiedenes – beide wörtlich aufgeführt, weil sie bereits
verwechselt wurden:

| Meldung beim Laden | Bedeutung |
|---|---|
| `The tied weights mapping … but both are present in the checkpoints with different values, so we will NOT tie them` | **harmlos.** Beide Schichten liegen getrennt im Checkpoint und werden übernommen; stand auch im ersten, funktionierenden Lauf. |
| `MISSING … newly initialized`, fehlende Schlüssel `encoder/decoder.embed_tokens.weight` | **kaputt.** Die Eingabe-Einbettung stammt aus dem Zufallsgenerator; das Modell hat 556 Mio. statt 300 Mio. Parameter. |

In einer Zahl sichtbar wird das am Verlustwert des ersten Schritts: richtig geladen startet das Basismodell hier bei
3–7 (abhängig von Aufgabe und Eingabelänge), mit zufälliger Einbettung bei 13,9 – reiner Zufall wären 12,4.
`train_mt5.py` druckt den Wert vor dem Training und warnt ab 11.

### Warum der erste Versuch Speicher erschöpft hat

Drei Dinge, die zusammenkommen – alle drei sind jetzt im Skript berücksichtigt:

1. **Batchzahlen sind pro Karte gemeint.** Der Trainer multipliziert `per_device_train_batch_size` selbst mit der
   Anzahl Karten (`train_batch_size = per_device × n_gpu`) und DataParallel teilt wieder auf. Wer den Sondenwert
   zusätzlich mit der Kartenanzahl multipliziert, trainiert mit der doppelten Batchgröße pro Karte → OOM.
2. **Die Sonde darf nicht durchschnittliche Texte probieren.** `group_by_length` steckt die längsten Beispiele in die
   ersten Schübe; die Sonde benutzt deshalb die längsten 64 Beispiele und prüft die tatsächlich belegten GiB, nicht nur
   „läuft es durch“.
3. **Die Ausgabe-Schicht ist der größte Brocken.** 250.100 Wörterbuchstücke × bis 256 Zieltokens: die Logits sind
   ~1 GiB im bf16-Autocast, und die Kreuzentropie rechnet in fp32 noch einmal ~2 GiB. Bei zwei Karten kommen die
   zusammengesammelten Ausgaben der zweiten Karte auf Karte 0 obendrauf – deshalb dort 3,5 GiB Sicherheitsabstand.
   DataParallel ist für Modelle mit so großem Vokabular speicherhungrig; zwei Karten mit je einem Prozess
   (`torchrun`, Linux) sind sparsamer.

`--max-train 5000 --epochs 1` ist der Pilot (unter einer Stunde), danach derselbe Befehl ohne `--max-train`.
Wiederholungen mit gleichen Einstellungen tokenisieren nicht erneut (`runs/…/tokenized/<hash>`).

## 7. Auswertung

`training/evaluate_meta.py` erzeugt für Testmaterialien die drei Felder und meldet

- Titel: Anteil exakter Treffer und Wortüberlappungs-F1,
- Beschreibung: ROUGE-L F1,
- Keywords: F1 auf den Komma-getrennten Schlagwörtern.

Zum Vergleich zweier Modelle: `--model runs/pilot/final runs/mt5-meta/final --n 200`. Als grobe Messlatte für ein
gesundes Modell auf diesem Datensatz: Titel exakt ≳ 0,25 und Wort-F1 ≳ 0,6, Keyword-F1 ≳ 0,5, ROUGE-L ≳ 0,3 – deutlich
darunter lohnt ein Blick in `samples_*.jsonl` (wiederholte Phrasen, leere Ausgaben, abgeschnittene Sätze).
Der reine eval_loss ist nur bedingt vergleichbar, weil sich die Aufgaben unterschiedlich schnell learningen.

Ergebnisse dieses Laufs: *(nach dem Training eintragen)*

| Modell | eval_loss | Titel exakt/F1 | Keyword-F1 | ROUGE-L |
|---|---|---|---|---|
| Pilot (5k, 1 Epoche) | | | | |
| voll (45k, 3 Epochen) | | | | |

## 8. Bekannte Fallen

- **Truncation**: bei `head`/1024 verlieren 15 % der Texte das Textende; deren Beschreibungen sind dadurch schwerer zu
  lernen. `condensed` löst das Problem der Länge, kostet Details.
- **transformers 5**: `group_by_length` und `pad_to_multiple_of` sind keine Trainingsargumente mehr
  (`train_sampling_strategy`, Padding über den Datenkollator). Das Skript erkennt beides Versionen.
- **Tokenisierung**: 135k Beispiele dauern ~10 Minuten (SentencePiece, vier Prozesse); Ergebnis wird gecacht.
- **Speicherverteilung**: `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` wird vom Skript gesetzt, weil die
  unterschiedlichen Textlängen sonst schnell fragmentierte Blöcke hinterlassen.
- **Windows**: `dataloader_num_workers` steht auf 0, weil Spawn-Prozesse teuer sind; unter Linux sind 4 schneller.
- **Echte Texte** sind länger und verrauschter (Navigation, Cookie-Hinweise) als die synthetischen Materialien – vor der
  Nutzung säubern; der Schnitt bei 1024 Tokens passiert in `predict.py` genauso wie im Training.
- Die Materialien stammen von einem einzigen LLM und einer Prompt-Familie; das Modell lernt diesen Stil.
