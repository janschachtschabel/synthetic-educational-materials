# synthetic-educational-materials

Erzeugt **Seeds** und **synthetische Bildungsmaterialien** (Volltext, Titel, Beschreibung, Keywords,
didaktische Frage-Antwort-Paare) aus offenen Vokabularen und Taxonomien. Struktur und Feldnamen folgen
dem OpenEduHub/edu-sharing-Metadatenschema. Jeder Wert trägt URI, Label und Herkunft.

Bildungsbereiche sind Module. Enthalten ist bisher **Hochschule** (Destatis-Fächersystematik,
OpenAlex-Themen, Lehrthemen aus GND und hbz-Katalog, Keyword-Belege aus WirLernenOnline). Weitere Bereiche lassen sich ergänzen.

Wie alles entsteht: [METHODIK.md](METHODIK.md). Die Daten im Einzelnen: [data/README.md](data/README.md).

## Installation

```
python -m pip install -r requirements.txt
```

Für die LLM-Schritte: `OPENAI_API_KEY` setzen, optional `OPENAI_BASE_URL` (jeder OpenAI-kompatible
Endpunkt), `LLM_MODEL` (Standard `gpt-6-luna`) und `LLM_WORKERS` (Standard 20). Alles lässt sich auch in
der Oberfläche oder per Parameter setzen.

## Oberfläche

```
streamlit run app.py
```

Reiter: **Seeds** (Anzahl je Bildungsbereich, Feld für die Gleichverteilung, Auswahl der Themen nach
Status, Quelle und Belegen, Zufalls-Seed),
**Materialien** (Start/Fortsetzen mit Fortschrittsanzeige), **Vorschau** (Seeds und fertige Materialien
durchblättern und durchsuchen: Titel, Beschreibung, Keywords, Volltext, Frage-Antwort-Paare,
Inhaltstyp-Prüfung, Metadaten und Herkunft), **Prompts** (bearbeiten und speichern),
**Mappings** (ansehen, herunterladen, neu bauen), **Methodik**.

## Kommandozeile

```
python -m sem info
python -m sem seeds --anzahl hochschule=30000 --gleichverteilung hochschule=thema --seed 42
python -m sem auswahl hochschule                # Filter (Status, Quellen, Belege) und Themenzahl
python -m sem seeds --anzahl hochschule=5000 --auswahl hochschule.fach_status=gesichert hochschule.belege=lehrbuch,wlo
python -m sem materialien --seeds output/seeds_hochschule30000_s42.jsonl --limit 100
python -m sem materialien --seeds output/seeds_hochschule30000_s42.jsonl          # setzt fort
python -m sem mappings hochschule --liste       # Mapping-Schritte anzeigen
python -m sem mappings hochschule --limit 50    # Probelauf aller Schritte mit 50 Themen
python -m sem mappings hochschule               # alle Schritte (LLM + lokale Embeddings), fortsetzbar
python -m sem mappings hochschule zusammenfuehren   # nur neu auswerten, z. B. nach Korrekturen
python -m sem mappings hochschule wlo --wlo export.csv   # WLO-Keywords als Belege (Export bleibt lokal)
python -m sem mappings hochschule gnd           # Themen aus GND und hbz-Katalog
```

LLM-Optionen für `materialien` und `mappings`: `--base-url`, `--api-key`, `--model`, `--workers`,
`--retries`. Für `mappings` zusätzlich `--stimmen` (Standard 3) und `--embedding-modell` (Standard
`intfloat/multilingual-e5-small`, läuft auf der CPU; wird beim ersten Lauf von Hugging Face geladen).

## Nur die Mappings nutzen

Alles unter `data/` ist ohne den Code nutzbar (JSON, JSONL, CSV, UTF-8):

- `data/vocabs/*.json`: OEH-Vokabulare flach aufbereitet (URI, Notation, Label, Oberbegriff, Ebene)
- `data/mappings/hochschule/themen_openalex.jsonl`: OpenAlex-Themen mit Destatis-Studienfach,
  -bereich und -fächergruppe, deutschen Labels, Keywords, Lehrebene sowie Status, Konfidenz und Belegen
  der Zuordnung
- `data/mappings/hochschule/studienfach_profile.jsonl`: Kurzprofil je Studienfach
- `data/mappings/hochschule/pruefliste_themen.csv`: Zuordnungen, die eine Durchsicht brauchen
- `data/mappings/hochschule/themen_gnd.jsonl`: Lehrthemen aus GND und hbz-Katalog mit Belegen
- `data/mappings/hochschule/studienfach_gnd_sachgruppe.jsonl`: Studienfach → GND-Sachgruppen
- `data/mappings/hochschule/openalex_teilgebiet_studienbereich.csv`: Handmapping der OpenAlex-Teilgebiete

## Aufbau

```
app.py                     Oberfläche (Streamlit)
sem/                       Paket: llm, seeds, generate, prompts, schema, vocabs
sem/mapping/               bereichsunabhängige Mapping-Bausteine (Embeddings, Abstimmung, Prüfliste)
sem/bereiche/<bereich>/    ein Modul je Bildungsbereich (Themen, Seeds, Mapping-Bau)
data/vocabs/               aufbereitete Vokabulare
data/mappings/<bereich>/   Mappings und Konfiguration je Bereich
prompts/                   editierbare Prompt-Vorlagen
output/                    erzeugte Seeds und Materialien (nicht versioniert)
```

## Lizenz

Code: Apache 2.0. Daten unter `data/`: aus CC0-Quellen abgeleitet, Herkunft siehe METHODIK.md.
