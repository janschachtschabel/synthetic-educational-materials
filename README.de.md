# synthetic-educational-materials

*[English](README.md) · Deutsch*

Erzeugt **synthetische deutsche Bildungsmaterialien** als Trainingsdaten für Metadatenmodelle. Jedes Material
hat einen Volltext von etwa einer A4-Seite und die Metadaten, die ein Repositorium wie
[WirLernenOnline](https://wirlernenonline.de) braucht: Titel, Beschreibung, Schlagworte, Inhaltstyp und didaktische
Frage-Antwort-Paare. Struktur und Feldnamen folgen dem OpenEduHub/edu-sharing-Metadatenschema.

```
offene Vokabulare ──► Themen je Fach ──► Seeds ──► Volltext ──► Titel, Beschreibung, Keywords, Fragen
(OEH, Destatis, ESCO,  (Mappings, einmal)  (ohne LLM)  (LLM)      (LLM, nur aus dem Volltext abgeleitet)
 GND, OpenAlex, RVK, DigComp, KMK …)
```

Fünf Bildungsbereiche sind eigene Module: **Schule**, **Hochschule**, **Berufliche Bildung**, **Elementarbereich**
und **Erwachsenenbildung**. Weitere Bereiche lassen sich ergänzen.

**Ergebnisse:** der Datensatz [synthetic-german-educational-materials-50k](https://huggingface.co/datasets/JanSchachtschabel/synthetic-german-educational-materials-50k)
(50.000 Materialien aus allen fünf Bereichen) und ein darauf trainiertes mT5-small-Modell, das aus einem Volltext
Titel, Beschreibung und Keywords erzeugt, auch auf der CPU ([training/](training/README.de.md)).

## Prinzipien

1. **Offene Quellen, nachvollziehbare Werte.** Themen, Fächer, Stufen und Inhaltstypen stammen aus offenen
   Vokabularen und Taxonomien. Jeder Wert trägt URI, Label und Herkunft (`synth:provenance`), wo nötig mit der
   geforderten Quellenangabe.
2. **Keine übernommenen Inhalte.** Aus vorhandenen Bildungsmaterialien wird kein Text übernommen. Echte
   Repositoriumsdaten (WLO, OERSI) dienen nur aggregiert: Keyword-×-Fach-Kombinationen als Beleg für Themen,
   auf GND-Sachbegriffe normalisiert, nie als Text.
3. **Metadaten aus dem Text.** Nur der Volltext-Schritt sieht das Thema des Seeds. Titel, Beschreibung und Keywords
   entstehen danach allein aus dem Volltext, also genau die Aufgabe, die ein Modell später lernen soll.
4. **Gemessene Sicherheit statt Raten.** Mappings verbinden lokale Embeddings mit mehreren LLM-Stimmen. Jede
   Zuordnung hat einen Status (`gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell`), Belege und eine
   Prüfliste; Handkorrekturen haben Vorrang. Seeds lassen sich nach Status, Quelle und Beleg filtern.
5. **Ausgewogen und reproduzierbar.** Seeds entstehen ohne LLM, gleich verteilt über Fächer oder Themen, mit festem
   Zufalls-Seed. Prompts werden per Hash festgehalten, lokale Daten per Fingerabdruck, und jeder Lauf lässt sich
   fortsetzen.
6. **Urheberrecht gewahrt.** Geschützte Quellen (z. B. Rahmenlehrpläne) werden nur lokal unter `data/lokal/` und nur
   als Kapitelcodes und -titel genutzt. Alles läuft auch ohne sie.
7. **Geprüfte Ausgaben.** Jede LLM-Antwort wird geprüft (Länge, Format, verbotene Zeichen, lesbare Titel statt
   Schlagwortketten) und bei Fehlern neu angefragt.

## Schnellstart

Python 3.11 oder neuer, [uv](https://docs.astral.sh/uv/) empfohlen. `OPENAI_API_KEY` (oder `LLM_ANBIETER=anthropic`
und `ANTHROPIC_API_KEY`) in der Umgebung setzen, dann:

```
uv sync --all-extras
uv run python app.py             # Oberfläche unter http://127.0.0.1:8501
```

Oder auf der Kommandozeile:

```
uv run python -m sem seeds --anzahl schule=1000 --seed 42
uv run python -m sem materialien --seeds output/seeds_schule1000_s42.jsonl --limit 50
```

## Dokumentation

| Dokument | Inhalt |
|---|---|
| [docs/usage.de.md](docs/usage.de.md) | Installation, Oberfläche, Kommandozeile, Flex und Batch, Fortsetzen, Datendateien |
| [METHODIK.md](METHODIK.md) | Quellen und Lizenzen, wie Mappings, Seeds und Materialien entstehen, bekannte Grenzen |
| [data/README.de.md](data/README.de.md) | Vokabulare und Mapping-Dateien im Einzelnen |
| [prompts/README.de.md](prompts/README.de.md) | die Prompt-Vorlagen |
| [training/README.de.md](training/README.de.md) | Hugging-Face-Export und mT5-Feintraining; gemessene Zahlen in [TRAININGSWISSEN.md](training/TRAININGSWISSEN.md) |

## Lizenz

Code: Apache 2.0. Daten unter `data/`: aus CC0-Quellen abgeleitet; ESCO-Labels und -Beschreibungen unter
den Weiterverwendungsbedingungen der EU-Kommission (Quelle: ESCO, Europäische Kommission), KldB mit
Quellenangabe der Bundesagentur für Arbeit, Codes und Bezeichnungen der Volkshochschul-Statistik aus dem
DIE-Statistikbericht (CC BY 4.0 DE), aus dem Rahmencurriculum für Integrationskurse nur Kapitelcodes und -titel
(urheberrechtlich geschützt, in jeder Herkunftsangabe zitiert). Herkunft siehe METHODIK.md.
