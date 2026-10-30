# Data

*English · [Deutsch](README.de.md)*

## `vocabs/`

One file `<name>.json` per vocabulary:

```json
{"scheme": {"name", "uri", "title", "source", "public_url", "license", "retrieved"},
 "concepts": [{"uri", "notation", "label", "broader", "level"}]}
```

`broader` is the URI of the broader concept, and `level` is the depth (0 = top level). Where the vocabulary provides them,
`definition` and `note` are also included (KldB: short description and typical activities). Reload with
`python scripts/fetch_vocabs.py` (all) or `python scripts/fetch_vocabs.py kldb` (one).

## `mappings/berufsbildung/`

| File | Contents |
|---|---|
| `berufe.jsonl` | one ESCO occupation per line, see below |
| `kompetenzen.jsonl` | one ESCO skill per line: `uri`, `label`, `alt_labels`, `beschreibung`, `art` (`wissen`, `faehigkeit`), `reichweite` (`berufsspezifisch`, `branchenspezifisch`, `branchenuebergreifend`, `querschnitt`), `isced_f`, `essenziell_fuer`/`optional_fuer` (number of occupations), `oeh_uri` (OEH escoSkills), `belege.wlo` |
| `pruefliste_berufe.csv` | occupations with a reason for review, empty correction columns |
| `korrekturen.csv` | editorial corrections (`korrektur_codes` = KldB (Classification of Occupations) codes separated by `|`, `-` = exclude, `korrektur_qualifizierung`, `kommentar`), take precedence over the LLM |
| `inhaltstypen.json` | content types used from OEH `new_lrt`, with text type, media format, skill types, and target groups |
| `niveaus.json` | DQR levels with qualification pathway and educational level |
| `optionale_felder.json` | values and proportions of the optional control fields (learning location, cross-cutting topic …) |

Occupation structure:

```json
{"uri": "http://data.europa.eu/esco/occupation/…", "label": "…", "alt_labels": ["…"], "beschreibung": "…",
 "isco": "7212", "esco_code": "7212.1",
 "berufsuntergruppe": [{"uri": "http://w3id.org/openeduhub/vocabs/kldb/2442", "notation": "2442", "label": "…"}],
 "berufsgruppe": [{"uri", "notation", "label"}], "berufshauptgruppe": […], "berufsbereich": […],
 "qualifizierung": "ausbildung | fortbildung | studium | anlernen",
 "essenziell": ["http://data.europa.eu/esco/skill/…"], "optional": ["…"],
 "quelle": {"name": "ESCO", "uri", "lizenz"}, "belege": {"wlo": {"datensaetze", "nodes", "kldb"}},
 "mapping": {"methode", "modell", "embedding_modell", "stimmen", "datum", "prompt_hash",
             "status", "konfidenz", "handmapping_bestaetigt", "kandidaten",
             "kldb": [{"uri", "notation", "label", "stimmen", "an_erster_stelle", "konfidenz", "aehnlichkeit",
                       "begruendungen", "belegbegriffe_im_text"}], "verworfen": {},
             "qualifizierung": {"status", "konfidenz", "stimmen": {"ausbildung": 3, …}, "begruendungen"}}}
```

Topics (occupation × skill) are generated when loading from both files and are not stored.

## `mappings/elementar/`

| File | Contents |
|---|---|
| `bildungsbereiche.json` | the seven education sectors of the Gemeinsamer Rahmen der Länder (Common Framework of the Länder; 2022 edition), with `id`, label, short description, and `closeMatch` to OEH `discipline` |
| `themen_elementar.jsonl` | one topic per line: `id` (GND URI), `label_de` (without GND qualifier), `label_gnd`, `synonyme`, `definition`, `oberbegriffe`, `gnd_sachgruppen`, `art` (`kinderthema`, `fachthema`), `altersgruppe` (`u3`, `ue3`, `beide`), `bildungsbereich` [{`id`, `uri`, `label`}], `herkunft` (`llm`, `wlo`), `quelle`, `belege.wlo`, `mapping` (status, confidence, and votes for sector, suitability, and age) |
| `pruefliste_themen.csv`, `korrekturen.csv` | review (`korrektur_codes` = education-sector IDs, optionally `kinderthema`/`fachthema`, `-` = exclude; `korrektur_alter`) |
| `inhaltstypen.json`, `niveaus.json`, `optionale_felder.json` | content types with topic type and target groups, age levels, optional fields |

## `mappings/schule/`

| File | Contents |
|---|---|
| `themen_schule.jsonl` | one topic per line: `id` (URI in the OEH oehTopics vocabulary), `label_de`, `portal`, `portal_uri`, `pfad`, `ebene`, `definition`, `keywords`, `faecher` (OEH discipline), `art` (`unterrichtsthema`, `lehrkraftthema`), `stufen` (`primar`, `sek1`, `sek2`), `gnd` (exact GND match), `quelle`, `belege.wlo` (records per level, node IDs), `mapping` (status, confidence, votes, `wlo_bestaetigt`) |
| `pruefliste_themen.csv`, `korrekturen.csv` | review (`korrektur_codes` = levels separated by `|`, `-` = exclude; `korrektur_art`) |
| `inhaltstypen.json`, `niveaus.json`, `optionale_felder.json` | content types with topic type, target groups, levels, and subject portals; year groups; optional fields (including area of requirement) |

## `mappings/erwachsenenbildung/`

| File | Contents |
|---|---|
| `programmbereiche.json` | 7 programme areas and 85 subject areas from the Volkshochschul-Statistik (Adult Education Center Statistics) (`notation`, `label`, `uri`, `uebergreifend` for the collective entries x.00) |
| `sprachkurs_themen.json` | fields of action and learning objective areas from the Rahmencurriculum für Integrationskurse (Framework Curriculum for Integration Courses) (`code`, `handlungsfeld`, `titel`, `label`, `fachgebiete`) |
| `themen_erwachsenenbildung.jsonl` | one topic per line: `id` (GND URI, Rahmencurriculum code, or oehTopics URI), `label_de`, `art` (`kursthema`, `kursleitungsthema`), `herkunft` (`gnd`, `rahmencurriculum`, `schulthemen`), `fachgebiet` and `programmbereich` [{`notation`, `uri`, `label`}] (foreign languages as `4.fs`), `quelle`, `belege.wlo`, `mapping` |
| `pruefliste_themen.csv`, `korrekturen.csv` | review (`korrektur_codes` = subject areas, optionally `kursthema`/`kursleitungsthema`, `-` = exclude) |
| `inhaltstypen.json`, `niveaus.json`, `optionale_felder.json` | content types with topic type, target groups, and programme areas; levels per programme area (GER, Alpha-Level, qualifications); optional fields and target languages per subject area |

## `mappings/hochschule/`

| File | Contents |
|---|---|
| `themen_hochschule.jsonl` | complete set: all topics, one topic per line, see below. `art` = `openalex` or `gnd`; GND topics also have `gnd` (GND ID, type, subject group, broader concepts), `belege.katalog`, and `mapping.hochschulrelevanz`; topics with WLO evidence have `belege.wlo`. Intermediate results per source are generated during the build in the cache (`data/cache/hochschule_themen_*.jsonl`) and combined in the `themen` step |
| `studienfach_profile.jsonl` | short description, typical topics, and distinction for each field of study (LLM) |
| `pruefliste_themen.csv` | mappings with a reason for review, empty correction columns |
| `korrekturen.csv` | editorial review of both review lists (columns `id`, `label_de`, `korrektur_codes` separated by `|`, `-` = exclude, `korrektur_lehrebene`, `kommentar`, `prueflistengrund`), take precedence over the LLM |
| `pruefliste_gnd.csv` | GND topics requiring review |
| `gnd_ausgeschlossen.csv` | GND terms not classified as higher education teaching content (with sector and votes) |
| `studienfach_gnd_sachgruppe.jsonl` | bridge from field of study to GND subject groups, with votes |
| `openalex_teilgebiet_studienbereich.csv` | manual mapping from OpenAlex subfield to study area(s) |
| `inhaltstypen.json` | content types used from OEH `new_lrt`, with text type, media format, and teaching levels |
| `niveaus.json` | levels within higher education |
| `optionale_felder.json` | values and proportions of the optional control fields |

Topic structure:

```json
{"id": "https://openalex.org/T10001", "art": "openalex | grundlagen",
 "label_de": "…", "label_en": "…", "beschreibung_de": "…", "keywords_de": ["…"], "keywords_en": ["…"],
 "studienfach":   [{"uri": "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/n065", "notation": "065", "label": "…"}],
 "studienbereich": [{"uri", "notation", "label"}], "faechergruppe": [{"uri", "notation", "label"}],
 "lehrebene": "grundlagen | vertiefung | forschung",
 "quelle":  {"name", "uri", "lizenz"},
 "belege": {"katalog": {"titel", "lehrbuecher", "hochschulschriften", "quelle"},
            "wlo": {"quelle", "datensaetze", "faecher": [{"uri", "label", "datensaetze"}], "nodes": ["…"],
                    "keywords": ["…"], "fach_passt": true}},
 "mapping": {"methode", "modell", "embedding_modell", "stimmen", "datum", "prompt_hash",
             "status": "gesichert | wahrscheinlich | unsicher | redaktionell", "konfidenz": 1.0,
             "handmapping_bestaetigt": true, "kandidaten": 17,
             "faecher": [{"uri", "notation", "label", "stimmen": 3, "an_erster_stelle": 3, "konfidenz": 1.0,
                          "aehnlichkeit": 0.84, "begruendungen": ["…"], "belegbegriffe_im_text": ["…"]}],
             "verworfen": {"Fach": 1},
             "lehrebene": {"status", "konfidenz", "stimmen": {"grundlagen": 0, "vertiefung": 2, "forschung": 1},
                           "begruendungen": ["…"]}}}
```
