# synthetic-educational-materials

*[English](README.md) · Deutsch*

Erzeugt **Seeds** und **synthetische Bildungsmaterialien** (Volltext, Titel, Beschreibung, Keywords,
didaktische Frage-Antwort-Paare) aus offenen Vokabularen und Taxonomien. Struktur und Feldnamen folgen
dem OpenEduHub/edu-sharing-Metadatenschema. Jeder Wert trägt URI, Label und Herkunft.

Bildungsbereiche sind Module. Enthalten sind **Hochschule** (Destatis-Fächersystematik,
OpenAlex-Themen, Lehrthemen aus GND und hbz-Katalog, Keyword-Belege aus WirLernenOnline) und
**Berufliche Bildung** (ESCO-Berufe × Kompetenzen, KldB-Berufsfelder, Qualifizierungsweg und DQR-Niveau,
Handmapping über den BA-Umsteigeschlüssel ISCO → KldB, Lernfelder der KMK-Rahmenlehrpläne) und **Elementarbereich** (sieben Bildungsbereiche
des Gemeinsamen Rahmens der Länder, Kita-Themen als GND-Sachbegriffe aus LLM-Themenlisten und WLO-Keywords,
Eignung und Altersgruppe per Abstimmung) und **Schule** (Themenbäume der WLO-Fachportale und feinere Themen aus
WLO-Keywords je Fach als GND-Sachbegriffe, Primarstufe, Sekundarstufe I und II als Metadatum und Filter, KMK-Bildungsstandards und Förderschwerpunkt als
Metadaten) und **Erwachsenenbildung** (Programmbereiche und
Fachgebiete der Volkshochschul-Statistik, Kursthemen aus GND, Sprachkursthemen aus dem Rahmencurriculum für
Integrationskurse, Schulabschlusskurse, Grundbildung aus lea.-Alpha-Levels und DVV-Rahmencurricula). Weitere Bereiche lassen sich ergänzen.

Wie alles entsteht: [METHODIK.md](METHODIK.md). Die Daten im Einzelnen: [data/README.de.md](data/README.de.md).

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
**Materialien** (Start/Fortsetzen mit Fortschrittsanzeige, Ausgabesprache), **Vorschau** (Seeds und fertige Materialien
durchblättern und durchsuchen: Titel, Beschreibung, Keywords, Volltext, Frage-Antwort-Paare,
Inhaltstyp-Prüfung, Metadaten und Herkunft), **Prompts** (bearbeiten und speichern),
**Mappings** (ansehen, herunterladen, neu bauen), **Methodik**.

**Menüsprache:** Deutsch oder Englisch, umschaltbar oben in der Seitenleiste („Sprache / Language“);
Voreinstellung über `SEM_UI_SPRACHE=en`. Das betrifft nur die Bedienung; die Sprache der erzeugten Inhalte
wählt man getrennt davon im Reiter „Materialien“ (Ausgabesprache). Die Übersetzungen stehen in
`i18n/en.json` (deutscher Text als Schlüssel). Neue Oberflächentexte findet `python scripts/i18n_texte.py`;
mit `--uebersetzen` ergänzt es fehlende Übersetzungen per LLM. Ob die englische Dokumentation hinter der
deutschen zurückliegt, zeigt `python scripts/doku_paare.py` (Rückgabewert 1, wenn eine Fassung veraltet ist).

Seed-Dateien beginnen immer mit `seeds_`, erzeugte Materialien mit `materialien_` (beides unter
`output/`). Oberfläche und Kommandozeile ergänzen das Präfix selbst; die Auswahllisten zeigen nur die
passenden Dateien.

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
python -m sem auswahl berufsbildung             # Filter Berufsbildung (Qualifizierungsweg, Kompetenzart …)
python -m sem seeds --anzahl berufsbildung=10000 --gleichverteilung berufsbildung=beruf --seed 42
python -m sem mappings berufsbildung --wlo export.csv   # ESCO laden, KldB und Qualifizierung abstimmen, WLO-Belege
python -m sem mappings berufsbildung lernfeld --rlp ORDNER   # Lernfelder der KMK-Rahmenlehrpläne (Markdown) → ESCO-Berufe
python -m sem seeds --anzahl berufsbildung=2000 --auswahl berufsbildung.quellen=lernfeld --seed 42   # nur Lernfeld-Themen
python -m sem seeds --anzahl schule=5000 --auswahl schule.stufen=sek1 --seed 42   # nur Sekundarstufe I
python -m sem seeds --anzahl schule=5000 --auswahl schule.herkunft=themenbaum --seed 42   # nur Themenbäume, ohne Keyword-Themen
python -m sem mappings schule keywords --wlo export.csv   # Schulthemen aus WLO-Keywords (danach kw_gnd … kw_stufen)
python -m sem auswahl erwachsenenbildung        # Filter Erwachsenenbildung (Programmbereiche, Quellen, Status)
python -m sem seeds --anzahl erwachsenenbildung=2000 --auswahl erwachsenenbildung.quellen=gnd,rahmencurriculum --seed 42
python -m sem seeds --anzahl elementar=1000 schule=3000 berufsbildung=3000 hochschule=3000 erwachsenenbildung=1000 --seed 42
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --sprache en   # Materialien auf Englisch
```

**Themen auswählen:** Jeder Bereich hat eigene Filter für Quellen bzw. Herkunft, Abstimmungsstatus (Konfidenz:
`gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell`) und Belege; `python -m sem auswahl <bereich>` zeigt
sie mit der Zahl der Themen. Voreinstellung: alles außer `unsicher`. In der Oberfläche stehen dieselben Filter
im Reiter „Seeds“ unter jedem Bereich.

**Ausgabesprache:** Materialien entstehen standardmäßig auf Deutsch. `--sprache en` (auch `fr`, `es`, `it`, `nl`,
`pl`, `tr`, `uk`, `ru`, `ar`) oder „Ausgabesprache“ im Reiter „Materialien“ schreibt alle Felder in dieser
Sprache; der Bildungskontext bleibt Deutschland. Der Hinweis an das Modell steht in `prompts/ausgabesprache.md`,
die Sprache in `cclom:general_language`. Je Sprache eine neue Ausgabedatei wählen, die Fortsetzung prüft sie nicht.

LLM-Optionen für `materialien` und `mappings`: `--base-url`, `--api-key`, `--model`, `--workers`
(Standard 20, `LLM_WORKERS`), `--reasoning-effort` (Standard `low`, `LLM_REASONING_EFFORT`; `standard`
sendet den Parameter nicht), `--retries`. In der Oberfläche stehen Worker und Reasoning-Aufwand in der
Seitenleiste unter „Geschwindigkeit“. Für `mappings` zusätzlich `--stimmen` (Standard 3) und `--embedding-modell` (Standard
`intfloat/multilingual-e5-small`, läuft auf der CPU; wird beim ersten Lauf von Hugging Face geladen).

## Nur die Mappings nutzen

Alles unter `data/` ist ohne den Code nutzbar (JSON, JSONL, CSV, UTF-8):

- `data/vocabs/*.json`: OEH-Vokabulare flach aufbereitet (URI, Notation, Label, Oberbegriff, Ebene)
- `data/mappings/hochschule/themen_hochschule.jsonl`: alle Hochschulthemen in einer Datei (9.130:
  OpenAlex-Themen und GND-Lehrthemen) mit Destatis-Studienfach, -bereich und -fächergruppe, deutschen
  Labels, Keywords, Lehrebene sowie Herkunft (`art`, `quelle`), Status, Konfidenz und Belegen der
  Zuordnung; nach diesen Angaben filterbar (`python -m sem auswahl hochschule`)
- `data/mappings/hochschule/studienfach_profile.jsonl`: Kurzprofil je Studienfach
- `data/mappings/hochschule/pruefliste_*.csv`, `korrekturen.csv`: Zuordnungen für die Durchsicht und deren Ergebnis
- `data/mappings/hochschule/studienfach_gnd_sachgruppe.jsonl`: Studienfach → GND-Sachgruppen
- `data/mappings/hochschule/openalex_teilgebiet_studienbereich.csv`: Handmapping der OpenAlex-Teilgebiete
- `data/mappings/berufsbildung/berufe.jsonl`: 2.930 ESCO-Berufe mit KldB-Berufsuntergruppe (und Oberebenen),
  Qualifizierungsweg, Status, Konfidenz, Handmapping-Bestätigung und den URIs ihrer essenziellen und
  optionalen Kompetenzen
- `data/mappings/berufsbildung/kompetenzen.jsonl`: 12.539 ESCO-Kompetenzen (Label, Beschreibung, Art,
  Reichweite, ISCED-F-Feld, `oeh_uri` im OEH-Vokabular escoSkills, WLO-Belege)
- `data/mappings/berufsbildung/rahmenlehrplaene.jsonl`: 265 KMK-Rahmenlehrpläne mit 3.697 Lernfeldern
  (Nummer, Titel, Ausbildungsjahr, Zeitrichtwert; keine Zieltexte) und zugeordneten ESCO-Berufen
- `data/mappings/elementar/themen_elementar.jsonl`: 1.033 Kita-Themen (GND-URI, Synonyme, Definition,
  Themenart, Altersgruppe, Bildungsbereich, Herkunft, Status, WLO-Belege); `bildungsbereiche.json`: die
  sieben Bildungsbereiche mit closeMatch auf OEH-Schulfächer
- `data/mappings/schule/themen_schule.jsonl`: 7.569 Schulthemen: 3.153 aus den WLO-Themenbäumen und 4.416
  feinere Themen aus WLO-Keywords je Fach (als GND-Sachbegriffe normiert), mit Fachportal, Pfad, Fach,
  Themenart, Schulstufen, Herkunft, Status, WLO-Belegen und GND-Abgleich
- `data/mappings/erwachsenenbildung/themen_erwachsenenbildung.jsonl`: 1.917 Kursthemen mit Fachgebiet und
  Programmbereich der Volkshochschul-Statistik, Herkunft (`gnd`, `rahmencurriculum`, `schulthemen`, `grundbildung`), Status;
  `programmbereiche.json`: die Systematik mit 85 Fachgebieten

## Aufbau

```
app.py                     Oberfläche (Streamlit)
i18n/                      Übersetzungen der Oberfläche (en.json)
sem/                       Paket: llm, seeds, generate, prompts, schema, vocabs
sem/mapping/               bereichsunabhängige Mapping-Bausteine (Embeddings, Abstimmung, Prüfliste)
sem/bereiche/<bereich>/    ein Modul je Bildungsbereich (Themen, Seeds, Mapping-Bau)
data/vocabs/               aufbereitete Vokabulare
data/mappings/<bereich>/   Mappings und Konfiguration je Bereich
prompts/                   editierbare Prompt-Vorlagen
output/                    erzeugte Seeds und Materialien (nicht versioniert)
```

## Lizenz

Code: Apache 2.0. Daten unter `data/`: aus CC0-Quellen abgeleitet; ESCO-Labels und -Beschreibungen unter
den Weiterverwendungsbedingungen der EU-Kommission (Quelle: ESCO, Europäische Kommission), KldB mit
Quellenangabe der Bundesagentur für Arbeit, Codes und Bezeichnungen der Volkshochschul-Statistik aus dem
DIE-Statistikbericht (CC BY 4.0 DE), aus dem Rahmencurriculum für Integrationskurse nur Kapitelcodes und -titel
(urheberrechtlich geschützt, in jeder Herkunftsangabe zitiert). Herkunft siehe METHODIK.md.
