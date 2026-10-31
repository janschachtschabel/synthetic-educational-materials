# synthetic-educational-materials

*English · [Deutsch](README.de.md)*

Generates **seeds** and **synthetic educational materials** (full text, titles, descriptions, keywords,
didactic question-answer pairs) from open vocabularies and taxonomies. Structure and field names follow
the OpenEduHub/edu-sharing metadata schema. Each value has a URI, label, and provenance.

Education sectors are modules. Included are **higher education** (Destatis-Fächersystematik,
OpenAlex topics, teaching topics from GND and the hbz catalog, keyword evidence from WirLernenOnline),
**vocational education and training** (ESCO occupations × skills, KldB occupational fields, qualification
pathway and DQR level, manual mapping via the BA conversion key ISCO → KldB, learning fields of the KMK
framework curricula), **early childhood education**
(seven educational areas of the Gemeinsamer Rahmen der Länder, daycare topics as GND subject headings from
LLM topic lists and WLO keywords, suitability and age group determined by vote), **school** (topic trees
from WLO subject portals plus finer topics from WLO keywords per subject as GND subject headings, primary,
lower secondary and upper secondary levels as metadata and filters, KMK educational standards and
special-needs focus as metadata),
and **adult education** (program areas and subject areas from the Volkshochschul-Statistik, course topics
from GND, language course topics from the Rahmencurriculum für Integrationskurse, school-leaving
qualification courses, basic education from the lea. Alpha-Levels and DVV framework curricula). More sectors can be added.

How everything is created: [METHODOLOGY.md](METHODOLOGY.md). The data in detail: [data/README.md](data/README.md).

## Installation

Python 3.11 or newer. Recommended with [uv](https://docs.astral.sh/uv/), which installs the versions from `uv.lock`:

```
uv sync --all-extras
uv run python app.py
```

Without local embeddings and test tools, `uv sync` is enough (extras: `mapping` = sentence-transformers,
`dev` = pytest, ruff). Commands then run with `uv run …`, e.g. `uv run python -m sem info`. After changing the
dependencies in `pyproject.toml`, renew the lock file with `uv lock`.

Alternatively with pip (versions not pinned):

```
python -m pip install -r requirements.txt
```

This installs the package with all extras from `pyproject.toml`; without local embeddings and test tools,
`python -m pip install -e .` is enough.

For the LLM steps: set `OPENAI_API_KEY`; optionally set `OPENAI_BASE_URL` (any OpenAI-compatible
endpoint), `LLM_MODEL` (default `gpt-6-luna`), and `LLM_WORKERS` (default 20). Everything can also be set
in the user interface or via parameters.

## User interface

```
python app.py            # opens http://127.0.0.1:8501 in the browser; --port, --ohne-browser
```

A web interface built with FastAPI and HTMX (`sem/web/`): the server delivers finished HTML pages, and HTMX swaps
only the parts that change (status display, dependent form fields, preview). HTMX is part of the repository
(`sem/web/static/`); nothing is loaded from the internet.

The interface is only reachable from this computer (server on `127.0.0.1`), because it has no login and can start
LLM runs with the key from the environment. It rejects requests with a foreign host name and changes coming from
other websites. It uses the key from
`OPENAI_API_KEY` only for the base URL from `OPENAI_BASE_URL`; for a different base URL, enter a key.

Tabs: **Seeds** (number per education sector, field for even distribution, topic selection by
status, source, and evidence, random seed; the summary shows topics and possible combinations of the selection per
sector compared with the maximum over all filter values, how often a combination occurs on average and what the
filters exclude),
**Materials** (start a new run or resume an unfinished one, status display), **Preview** (browse and search seeds and
completed materials: titles, descriptions, keywords, full text, question-answer pairs, content type check, metadata,
and provenance; reads directly from the file, even with several hundred MB), **Prompts** (edit and save),
**Mappings** (separate: view and download data; rebuild for operators, marking which step needs the LLM, a WLO export,
embeddings or local data), **Methodology**, **Settings** (LLM connection, workers, reasoning effort,
Flex; saved in `output/.oberflaeche.json`, the API key only in the server's memory). The prompt
templates themselves are in German.

**Status display:** phase (starting, running, stopping, no response, paused, finished, error), progress bar,
succeeded, failed, open, in progress, speed, time remaining, API requests and retries, plus the age of the status.
It updates every second while the run is alive; the header shows which runs are currently working. If a live run
writes no status for over a minute, it reports “no response”.

**Keyboard and screen reader use:** all fields are labelled, groups have a heading (`fieldset`/`legend`), focus is
always visible, and a link skips to the content. Phase changes of a run are announced (`aria-live`), and the buttons of
the status display keep their focus while the numbers change. Colours have a contrast of at least 4.5:1 in light and
dark mode, and no message relies on colour alone.

**Interface language:** German or English, switchable at the top right;
default can be set with `SEM_UI_SPRACHE=en`. This affects only the interface; the language of generated
content is selected separately in the “Materials” tab (output language). The translations are in
`i18n/en.json` (German text as keys). To find new interface text, run `python scripts/i18n_texte.py`;
`--uebersetzen` adds missing translations using an LLM. Whether the English documentation lags behind the
German version is shown by `python scripts/doku_paare.py` (exit code 1 if a version is outdated).

Seed files always start with `seeds_`, generated materials with `materialien_` (both under
`output/`). The user interface and command line add the prefix themselves; the selection lists show only
the matching files.

## Command line

```
python -m sem info
python -m sem seeds --anzahl hochschule=30000 --gleichverteilung hochschule=thema --seed 42
python -m sem auswahl hochschule                # Filters (status, sources, evidence) and number of topics
python -m sem seeds --anzahl hochschule=5000 --auswahl hochschule.fach_status=gesichert hochschule.belege=lehrbuch,wlo
python -m sem materialien --seeds output/seeds_hochschule30000_s42.jsonl --limit 100
python -m sem materialien --seeds output/seeds_hochschule30000_s42.jsonl          # resumes
python -m sem mappings hochschule --liste       # Show mapping steps
python -m sem mappings hochschule --limit 50    # Test run of all steps with 50 topics
python -m sem mappings hochschule               # All steps (LLM + local embeddings), resumable
python -m sem mappings hochschule zusammenfuehren   # Re-evaluate only, e.g. after corrections
python -m sem mappings hochschule wlo --wlo export.csv   # WLO keywords as evidence (export stays local)
python -m sem mappings hochschule gnd           # Topics from GND and the hbz catalog
python -m sem auswahl berufsbildung             # Vocational education and training filters (qualification pathway, skill type …)
python -m sem seeds --anzahl berufsbildung=10000 --gleichverteilung berufsbildung=beruf --seed 42
python -m sem mappings berufsbildung --wlo export.csv   # Load ESCO, vote on KldB and qualification, WLO evidence
python -m sem mappings berufsbildung lernfeld --rlp FOLDER   # Learning fields of the KMK framework curricula (Markdown) → ESCO occupations
python -m sem seeds --anzahl berufsbildung=2000 --auswahl berufsbildung.quellen=lernfeld --seed 42   # learning-field topics only (needs data/lokal/)
python -m sem seeds --anzahl schule=5000 --auswahl schule.stufen=sek1 --seed 42   # Secondary level I only
python -m sem seeds --anzahl schule=5000 --auswahl schule.herkunft=themenbaum --seed 42   # topic trees only, without keyword topics
python -m sem mappings schule keywords --wlo export.csv   # school topics from WLO keywords (then kw_gnd … kw_stufen)
python -m sem auswahl erwachsenenbildung        # Adult education filters (program areas, sources, status)
python -m sem seeds --anzahl erwachsenenbildung=2000 --auswahl erwachsenenbildung.quellen=gnd,schulthemen --seed 42
python -m sem seeds --anzahl elementar=1000 schule=3000 berufsbildung=3000 hochschule=3000 erwachsenenbildung=1000 --seed 42
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --sprache en   # materials in English
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --flex         # half price, slower
python -m sem materialien --seeds output/seeds_schule5000_s42.jsonl --batch --warten   # batch, until everything is done
python -m sem laeufe                                # runs that did not reach their target
python -m sem materialien --fortsetzen schule5000_s42   # resume one of them with its settings
```

**Selecting topics:** each sector offers its own filters for sources/origin, voting status (confidence:
`gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell`) and evidence; `python -m sem auswahl <bereich>`
lists them with the number of topics. Default: everything except `unsicher`. In the user interface, the same
filters are in the “Seeds” tab under each sector.

**Output language:** materials are written in German by default. `--sprache en` (also `fr`, `es`, `it`, `nl`,
`pl`, `tr`, `uk`, `ru`, `ar`) or “Output language” in the “Materials” tab writes all fields in that language;
the education context stays Germany. The note to the model is in `prompts/ausgabesprache.md`; the language is
stored in `cclom:general_language`. Use a new output file per language, because resuming does not check it.

**Half price: Flex or batch** (OpenAI only). `--flex` or “Flex-Verarbeitung” in the sidebar generates material by
material as usual, at half price but slower (measured on 30 Oct 2026: 50–90 s instead of about 30 s per material).
If Flex has no capacity (error 429), a request is retried twice and then sent at the standard price; how often is
recorded per material under `synth:generierung.tokens.aufrufe_standard`. `--batch` or “Verarbeitung: Batch” in the
“Materials” tab sends the requests collected to the Batch API: one round per stage (full text, title, description,
keywords, completion), each up to 24 hours, often faster. Every call collects finished results and sends the next
round; `--warten` repeats this until done. The state is kept in `output/materialien_<name>.batch/`. Prompts, checks
and results are the same in all cases; `synth:generierung.verarbeitung` records `flex` or `batch`.

**Interrupted runs:** every run records in `output/materialien_<name>.lauf.json` what it is meant to produce (seed
file, number, language, model, processing). If it did not reach its target, e.g. after an internet interruption or
with failed materials, it is listed by `python -m sem laeufe` and in the “Materials” tab under “Unfertigen Lauf
fortsetzen” (resume an unfinished run). The run's settings are shown there; workers and Standard or Flex are chosen
before starting. “Resume” (`--fortsetzen NAME`) continues with the same settings and skips finished materials,
“Start new” begins under a new name. The lock file of a crashed run is taken over when resuming.

**Runs from the user interface** (seeds, materials, batch rounds, mappings) start as a separate process
(`python -m sem … --stand`, see `sem/prozess.py`): the page stays responsive, and the run continues if the page is
closed or the server is restarted. If a run from the command line is working on an output, the interface shows this
and does not start a second one. The interface only reads
a small status file in `output/.prozesse/`; “Anhalten” (stop) creates a stop file there. The process output (error
messages) is in `output/.prozesse/<name>.log`.

LLM options for `materialien` and `mappings`: `--base-url`, `--api-key`, `--model`, `--workers`
(default 20, `LLM_WORKERS`), `--reasoning-effort` (default `low`, `LLM_REASONING_EFFORT`; `standard`
does not send the parameter), `--retries`. In the user interface, workers and reasoning effort are under
“Settings”; workers and Standard, Flex or Batch can also be chosen per run. For `mappings`, also: `--stimmen` (default 3) and `--embedding-modell` (default
`intfloat/multilingual-e5-small`, runs on the CPU; downloaded from Hugging Face on the first run).

## Using the mappings only

Everything under `data/` can be used without the code (JSON, JSONL, CSV, UTF-8):

- `data/vocabs/*.json`: OEH vocabularies prepared as flat files (URI, notation, label, broader term, level)
- `data/mappings/hochschule/themen_hochschule.jsonl`: all higher education topics in one file (9,130:
  OpenAlex topics and GND teaching topics) with Destatis study subject, subject area and subject group, German
  labels, keywords, teaching level, and mapping provenance (`art`, `quelle`), status, confidence, and evidence;
  filterable by these fields (`python -m sem auswahl hochschule`)
- `data/mappings/hochschule/studienfach_profile.jsonl`: short profile for each study subject
- `data/mappings/hochschule/pruefliste_*.csv`, `korrekturen.csv`: mappings for review and their outcomes
- `data/mappings/hochschule/studienfach_gnd_sachgruppe.jsonl`: study subject → GND subject groups
- `data/mappings/hochschule/openalex_teilgebiet_studienbereich.csv`: manual mapping of OpenAlex subfields
- `data/mappings/berufsbildung/berufe.jsonl`: 2,930 ESCO occupations with KldB occupational unit group (and higher-level groups),
  qualification pathway, status, confidence, manual mapping confirmation, and the URIs of their essential and
  optional skills
- `data/lokal/berufsbildung/rahmenlehrplaene.jsonl` (local only, not in the repository): 272 KMK framework curricula with 3,787 learning
  fields (number, title, training year, time allocation; no goal texts) and their mapped ESCO occupations
- `data/mappings/berufsbildung/kompetenzen.jsonl`: 12,539 ESCO skills (label, description, type,
  scope, ISCED-F field, `oeh_uri` in the OEH vocabulary escoSkills, WLO evidence)
- `data/mappings/elementar/themen_elementar.jsonl`: 1,033 daycare topics (GND URI, synonyms, definition,
  topic type, age group, education sector, provenance, status, WLO evidence); `bildungsbereiche.json`: the seven
  educational areas with closeMatch to OEH school subjects
- `data/mappings/schule/themen_schule.jsonl`: 7,569 school topics: 3,153 from the WLO topic trees and 4,416
  finer topics from WLO keywords per subject (normalized as GND subject headings), with subject portal, path,
  subject, topic type, school levels, provenance, status, WLO evidence, and GND alignment
- `data/mappings/erwachsenenbildung/themen_erwachsenenbildung.jsonl`: 1,466 course topics with subject area and
  program area from the Volkshochschul-Statistik, provenance (`gnd`, `schulthemen`), status;
  `programmbereiche.json`: the classification system with 85 subject areas
- `data/lokal/erwachsenenbildung/themen_lokal.jsonl` (local only, not in the repository): 451 further course
  topics from protected outlines (`rahmencurriculum` 117, `grundbildung` 334)

**Local data:** Complete outlines of copyrighted sources and the topics built from them are kept only in
`data/lokal/` (see [data/README.md](data/README.md)). Everything runs without them, just without the parts that
need them: no educational standards on school seeds, no learning-field topics, no integration-course and
basic-education topics in adult education. A warning is shown in that case. Seeds
(`synth:seed_parameter.lokale_daten`) and statistics (`.stats.json`) record the fingerprint of the local files
used (sha256, 12 characters; `null` = missing): the same random seed only yields the same seeds with the same
fingerprint.

## Structure

```
app.py                     starts the user interface (python app.py)
sem/web/                   User interface: FastAPI server, HTML templates, CSS, HTMX
i18n/                      User interface translations (en.json)
sem/                       Package: llm, seeds, generate, prompts, schema, vocabs
sem/mapping/               Sector-independent mapping components (embeddings, vote, review list)
sem/bereiche/<bereich>/    One module per education sector (topics, seeds, mapping build)
data/vocabs/               Prepared vocabularies
data/mappings/<bereich>/   Mappings and configuration for each sector
prompts/                   Editable prompt templates
output/                    Generated seeds and materials (not versioned)
```

## License

Code: Apache 2.0. Data under `data/`: derived from CC0 sources; ESCO labels and descriptions are subject to
the European Commission's reuse conditions (source: ESCO, European Commission), KldB with
attribution to the Bundesagentur für Arbeit, Volkshochschul-Statistik codes and labels from the DIE statistics
report (CC BY 4.0 DE), only chapter codes and titles from the Rahmencurriculum für Integrationskurse (copyrighted,
cited in every provenance entry). See METHODOLOGY.md for provenance.
