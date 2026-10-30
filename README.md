# synthetic-educational-materials

*English · [Deutsch](README.de.md)*

Generates **seeds** and **synthetic educational materials** (full text, titles, descriptions, keywords,
didactic question-answer pairs) from open vocabularies and taxonomies. Structure and field names follow
the OpenEduHub/edu-sharing metadata schema. Each value has a URI, label, and provenance.

Education sectors are modules. Included are **higher education** (Destatis-Fächersystematik,
OpenAlex topics, teaching topics from GND and the hbz catalog, keyword evidence from WirLernenOnline),
**vocational education and training** (ESCO occupations × skills, KldB occupational fields, qualification
pathway and DQR level, manual mapping via the BA conversion key ISCO → KldB), **early childhood education**
(seven educational areas of the Gemeinsamer Rahmen der Länder, daycare topics as GND subject headings from
LLM topic lists and WLO keywords, suitability and age group determined by vote), **school** (topic trees
from WLO subject portals, primary, lower secondary and upper secondary levels as metadata and filters),
and **adult education** (program areas and subject areas from the Volkshochschul-Statistik, course topics
from GND, language course topics from the Rahmencurriculum für Integrationskurse, school-leaving
qualification courses). More sectors can be added.

How everything is created: [METHODOLOGY.md](METHODOLOGY.md). The data in detail: [data/README.md](data/README.md).

## Installation

```
python -m pip install -r requirements.txt
```

For the LLM steps: set `OPENAI_API_KEY`; optionally set `OPENAI_BASE_URL` (any OpenAI-compatible
endpoint), `LLM_MODEL` (default `gpt-6-luna`), and `LLM_WORKERS` (default 20). Everything can also be set
in the user interface or via parameters.

## User interface

```
streamlit run app.py
```

Tabs: **Seeds** (number per education sector, field for even distribution, topic selection by
status, source, and evidence, random seed),
**Materials** (start/resume with progress indicator), **Preview** (browse and search seeds and completed
materials: titles, descriptions, keywords, full text, question-answer pairs,
content type check, metadata, and provenance), **Prompts** (edit and save),
**Mappings** (view, download, rebuild), **Methodology**. The prompt templates themselves are in German.

**Interface language:** German or English, switchable at the top of the sidebar (“Sprache / Language”);
default can be set with `SEM_UI_SPRACHE=en`. This affects only the interface; the language of generated
content is selected separately in the “Materials” tab (output language). The translations are in
`i18n/en.json` (German text as keys). To find new interface text, run `python scripts/i18n_texte.py`;
`--uebersetzen` adds missing translations using an LLM.

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
python -m sem seeds --anzahl schule=5000 --auswahl schule.stufen=sek1 --seed 42   # Secondary level I only
python -m sem seeds --anzahl elementar=1000 schule=3000 berufsbildung=3000 hochschule=3000 --seed 42
```

LLM options for `materialien` and `mappings`: `--base-url`, `--api-key`, `--model`, `--workers`
(default 20, `LLM_WORKERS`), `--reasoning-effort` (default `low`, `LLM_REASONING_EFFORT`; `standard`
does not send the parameter), `--retries`. In the user interface, workers and reasoning effort are in the
sidebar under “Geschwindigkeit” (“Speed”). For `mappings`, also: `--stimmen` (default 3) and `--embedding-modell` (default
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
- `data/mappings/berufsbildung/kompetenzen.jsonl`: 12,539 ESCO skills (label, description, type,
  scope, ISCED-F field, `oeh_uri` in the OEH vocabulary escoSkills, WLO evidence)
- `data/mappings/elementar/themen_elementar.jsonl`: 1,033 daycare topics (GND URI, synonyms, definition,
  topic type, age group, education sector, provenance, status, WLO evidence); `bildungsbereiche.json`: the seven
  educational areas with closeMatch to OEH school subjects
- `data/mappings/schule/themen_schule.jsonl`: 3,153 topics from WLO topic trees with subject portal, path, subject,
  topic type, school levels, status, WLO level evidence, and GND alignment
- `data/mappings/erwachsenenbildung/themen_erwachsenenbildung.jsonl`: 1,557 course topics with subject area and
  program area from the Volkshochschul-Statistik, provenance (`gnd`, `rahmencurriculum`, `schulthemen`), status;
  `programmbereiche.json`: the classification system with 85 subject areas

## Structure

```
app.py                     User interface (Streamlit)
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
attribution to the Bundesagentur für Arbeit. See METHODOLOGY.md for provenance.
