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

Python 3.11 oder neuer. Empfohlen mit [uv](https://docs.astral.sh/uv/), das die Versionen aus `uv.lock` installiert:

```
uv sync --all-extras
uv run python app.py
```

Ohne lokale Embeddings und Testwerkzeuge reicht `uv sync` (Extras: `mapping` = sentence-transformers,
`dev` = pytest, ruff). Befehle laufen dann mit `uv run …`, z. B. `uv run python -m sem info`. Nach einer
Änderung der Abhängigkeiten in `pyproject.toml` die Lock-Datei mit `uv lock` erneuern.

Alternativ mit pip (Versionen nicht festgelegt):

```
python -m pip install -r requirements.txt
```

Das installiert das Paket mit allen Extras aus `pyproject.toml`; ohne lokale Embeddings und Testwerkzeuge
reicht `python -m pip install -e .`.

Für die LLM-Schritte: `OPENAI_API_KEY` setzen, optional `OPENAI_BASE_URL` (jeder OpenAI-kompatible
Endpunkt), `LLM_MODEL` (Standard `gpt-6-luna`) und `LLM_WORKERS` (Standard 20). Alles lässt sich auch in
der Oberfläche oder per Parameter setzen.

## Oberfläche

```
python app.py            # öffnet http://127.0.0.1:8501 im Browser; --port, --ohne-browser
```

Eine Weboberfläche aus FastAPI und HTMX (`sem/web/`): Der Server liefert fertige HTML-Seiten, HTMX tauscht nur
die Teile aus, die sich ändern (Statusanzeige, abhängige Formularfelder, Vorschau). HTMX liegt im Repo
(`sem/web/static/`), es wird nichts aus dem Netz geladen.

Die Oberfläche ist nur auf diesem Rechner erreichbar (Server auf `127.0.0.1`), weil sie keine Anmeldung hat und
LLM-Läufe mit dem Key aus der Umgebung starten kann. Anfragen mit fremdem Host-Namen und Änderungen von fremden
Seiten weist sie ab. Den Key aus `OPENAI_API_KEY`
verwendet sie nur für die Base-URL aus `OPENAI_BASE_URL`; für eine andere Base-URL ist ein Key einzutragen.

Reiter: **Seeds** (Anzahl je Bildungsbereich, Feld für die Gleichverteilung, Auswahl der Themen nach
Status, Quelle und Belegen, Zufalls-Seed, Zusammenfassung vor dem Start),
**Materialien** (neuen Lauf starten oder unfertigen Lauf fortsetzen, Statusanzeige), **Vorschau** (Seeds und fertige
Materialien durchblättern und durchsuchen: Titel, Beschreibung, Keywords, Volltext, Frage-Antwort-Paare,
Inhaltstyp-Prüfung, Metadaten und Herkunft; blättert direkt in der Datei, auch bei mehreren hundert MB),
**Prompts** (bearbeiten und speichern), **Mappings** (ansehen, herunterladen, neu bauen), **Methodik**,
**Einstellungen** (LLM-Anbindung, Worker, Reasoning-Aufwand, Flex, Embedding-Modell; gespeichert in
`output/.oberflaeche.json`, der API-Key nur im Speicher des Servers).

**Statusanzeige:** Phase (startet, läuft, hält an, keine Rückmeldung, pausiert, fertig, Fehler), Fortschrittsbalken,
erfolgreich, fehlgeschlagen, offen, in Arbeit, Tempo, Restzeit, API-Anfragen und Wiederholungen, dazu das Alter des
Stands. Sie aktualisiert sich jede Sekunde, solange der Lauf lebt; oben in der Kopfzeile steht, welche Läufe gerade
arbeiten. Schreibt ein lebender Lauf über eine Minute keinen Stand, meldet sie „keine Rückmeldung“.

**Bedienung ohne Maus und mit Screenreader:** Alle Felder sind beschriftet, Gruppen haben eine Überschrift
(`fieldset`/`legend`), der Fokus ist immer sichtbar, ein Link springt zum Inhalt. Phasenwechsel eines Laufs werden
angesagt (`aria-live`), die Knöpfe der Statusanzeige behalten den Fokus, während sich die Zahlen ändern. Farben
haben in hellem und dunklem Modus mindestens den Kontrast 4,5:1, und keine Meldung hängt nur an der Farbe.

**Menüsprache:** Deutsch oder Englisch, umschaltbar oben rechts;
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
python -m sem seeds --anzahl berufsbildung=2000 --auswahl berufsbildung.quellen=lernfeld --seed 42   # nur Lernfeld-Themen (braucht data/lokal/)
python -m sem seeds --anzahl schule=5000 --auswahl schule.stufen=sek1 --seed 42   # nur Sekundarstufe I
python -m sem seeds --anzahl schule=5000 --auswahl schule.herkunft=themenbaum --seed 42   # nur Themenbäume, ohne Keyword-Themen
python -m sem mappings schule keywords --wlo export.csv   # Schulthemen aus WLO-Keywords (danach kw_gnd … kw_stufen)
python -m sem auswahl erwachsenenbildung        # Filter Erwachsenenbildung (Programmbereiche, Quellen, Status)
python -m sem seeds --anzahl erwachsenenbildung=2000 --auswahl erwachsenenbildung.quellen=gnd,schulthemen --seed 42
python -m sem seeds --anzahl elementar=1000 schule=3000 berufsbildung=3000 hochschule=3000 erwachsenenbildung=1000 --seed 42
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --sprache en   # Materialien auf Englisch
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --flex         # halber Preis, langsamer
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --batch --warten   # Batch, bis alles fertig ist
python -m sem laeufe                                # Läufe, die ihr Ziel nicht erreicht haben
python -m sem materialien --fortsetzen schule5000_s42   # einen davon mit seinen Einstellungen fortsetzen
```

**Themen auswählen:** Jeder Bereich hat eigene Filter für Quellen bzw. Herkunft, Abstimmungsstatus (Konfidenz:
`gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell`) und Belege; `python -m sem auswahl <bereich>` zeigt
sie mit der Zahl der Themen. Voreinstellung: alles außer `unsicher`. In der Oberfläche stehen dieselben Filter
im Reiter „Seeds“ unter jedem Bereich.

**Ausgabesprache:** Materialien entstehen standardmäßig auf Deutsch. `--sprache en` (auch `fr`, `es`, `it`, `nl`,
`pl`, `tr`, `uk`, `ru`, `ar`) oder „Ausgabesprache“ im Reiter „Materialien“ schreibt alle Felder in dieser
Sprache; der Bildungskontext bleibt Deutschland. Der Hinweis an das Modell steht in `prompts/ausgabesprache.md`,
die Sprache in `cclom:general_language`. Je Sprache eine neue Ausgabedatei wählen, die Fortsetzung prüft sie nicht.

**Halber Preis: Flex oder Batch** (nur OpenAI). `--flex` bzw. „Flex-Verarbeitung“ in der Seitenleiste erzeugt wie
gewohnt Material für Material, zum halben Preis, aber langsamer (gemessen am 30.10.2026: 50–90 s statt rund 30 s je
Material). Hat Flex keine Kapazität (Fehler 429), wird eine Anfrage zweimal wiederholt und dann zum Standardpreis
gestellt; wie oft, steht je Material unter `synth:generierung.tokens.aufrufe_standard`. `--batch` bzw.
„Verarbeitung: Batch“ im Reiter „Materialien“ schickt die Anfragen gesammelt an die Batch-Schnittstelle: eine Runde je
Stufe (Volltext, Titel, Beschreibung, Keywords, Abschluss), jede bis zu 24 Stunden, oft schneller. Jeder Aufruf holt
Fertiges ab und schickt die nächste Runde; `--warten` wiederholt das bis zum Ende. Der Stand liegt in
`output/materialien_<name>.batch/`. Prompts, Prüfungen und Ergebnis sind in allen Fällen gleich;
`synth:generierung.verarbeitung` vermerkt `flex` oder `batch`.

**Abgebrochene Läufe:** Jeder Lauf hält in `output/materialien_<name>.lauf.json` fest, was er erzeugen soll (Seed-Datei,
Anzahl, Sprache, Modell, Verarbeitung). Hat er sein Ziel nicht erreicht, etwa nach einer Internet-Unterbrechung oder
mit fehlgeschlagenen Materialien, steht er in `python -m sem laeufe` und im Reiter „Materialien“ unter „Unfertigen
Lauf fortsetzen“. Dort stehen die Einstellungen des Laufs; Worker und Standard oder Flex wählt man vor dem Start.
„Fortsetzen“ (`--fortsetzen NAME`) macht mit denselben Einstellungen weiter und überspringt Fertiges, „Neu starten“
beginnt unter neuem Namen. Die Sperrdatei eines abgestürzten Laufs wird beim Fortsetzen übernommen.

**Läufe aus der Oberfläche** (Seeds, Materialien, Batch-Runden, Mappings) starten als eigener Prozess
(`python -m sem … --stand`, siehe `sem/prozess.py`): Die Seite bleibt flüssig, und der Lauf geht weiter, wenn sie
geschlossen oder der Server neu gestartet wird. Arbeitet ein Lauf von der Kommandozeile an einer Ausgabe, zeigt
die Oberfläche das an und startet keinen zweiten. Die Oberfläche
liest nur eine kleine Stand-Datei in `output/.prozesse/`; „Anhalten“ legt dort eine Stop-Datei an. Die Ausgabe des
Prozesses (Fehlermeldungen) steht in `output/.prozesse/<name>.log`.

LLM-Optionen für `materialien` und `mappings`: `--base-url`, `--api-key`, `--model`, `--workers`
(Standard 20, `LLM_WORKERS`), `--reasoning-effort` (Standard `low`, `LLM_REASONING_EFFORT`; `standard`
sendet den Parameter nicht), `--retries`. In der Oberfläche stehen Worker und Reasoning-Aufwand unter
„Einstellungen“; Worker und Standard, Flex oder Batch wählt man außerdem je Lauf. Für `mappings` zusätzlich `--stimmen` (Standard 3) und `--embedding-modell` (Standard
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
- `data/lokal/berufsbildung/rahmenlehrplaene.jsonl` (nur lokal, nicht im Repo): 272 KMK-Rahmenlehrpläne mit 3.787 Lernfeldern
  (Nummer, Titel, Ausbildungsjahr, Zeitrichtwert; keine Zieltexte) und zugeordneten ESCO-Berufen
- `data/mappings/elementar/themen_elementar.jsonl`: 1.033 Kita-Themen (GND-URI, Synonyme, Definition,
  Themenart, Altersgruppe, Bildungsbereich, Herkunft, Status, WLO-Belege); `bildungsbereiche.json`: die
  sieben Bildungsbereiche mit closeMatch auf OEH-Schulfächer
- `data/mappings/schule/themen_schule.jsonl`: 7.569 Schulthemen: 3.153 aus den WLO-Themenbäumen und 4.416
  feinere Themen aus WLO-Keywords je Fach (als GND-Sachbegriffe normiert), mit Fachportal, Pfad, Fach,
  Themenart, Schulstufen, Herkunft, Status, WLO-Belegen und GND-Abgleich
- `data/mappings/erwachsenenbildung/themen_erwachsenenbildung.jsonl`: 1.466 Kursthemen mit Fachgebiet und
  Programmbereich der Volkshochschul-Statistik, Herkunft (`gnd`, `schulthemen`), Status;
  `programmbereiche.json`: die Systematik mit 85 Fachgebieten
- `data/lokal/erwachsenenbildung/themen_lokal.jsonl` (nur lokal, nicht im Repo): 451 weitere Kursthemen aus
  geschützten Gliederungen (`rahmencurriculum` 117, `grundbildung` 334)

**Lokale Daten:** Vollständige Gliederungen urheberrechtlich geschützter Vorlagen und die daraus gebauten
Themen liegen nur in `data/lokal/` (siehe [data/README.de.md](data/README.de.md)). Ohne sie läuft alles, nur
ohne die Teile, die sie brauchen: keine Bildungsstandards an Schul-Seeds, keine Lernfeld-Themen, in der
Erwachsenenbildung keine Integrationskurs- und Grundbildungs-Themen. Dann erscheint eine Warnung. Seeds
(`synth:seed_parameter.lokale_daten`) und Statistik (`.stats.json`) vermerken die Kennung der genutzten lokalen
Dateien (sha256, 12 Zeichen; `null` = fehlt): Gleicher Startwert ergibt nur bei gleicher Kennung dieselben Seeds.

## Aufbau

```
app.py                     startet die Oberfläche (python app.py)
sem/web/                   Oberfläche: FastAPI-Server, HTML-Vorlagen, CSS, HTMX
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
