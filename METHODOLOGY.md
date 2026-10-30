# Methodology

*English · [Deutsch](METHODIK.md)*

Brief description of how seeds and synthetic materials are created, so that results are transparent
and reproducible. Each record also carries the provenance of every field under `synth:provenance`.

## Overview

```
open vocabularies and taxonomies ──► Mappings (topics with subject assignments) ──► Seeds ──► Materials
     (OEH, Destatis, OpenAlex, GND,      data/mappings/<bereich>/                    without LLM  with LLM
      hbz-Katalog, WLO-Keywords)
```

1. **Mappings** assign topics from open sources to the subjects of an education sector. They are
   built once, stored in the repository, and can also be used independently.
2. **Seeds** combine one topic each with hard-coded and drawn metadata (educational level, subject,
   content type, level …). No LLM; reproducible using a random seed.
3. **Materials** are generated from seeds using an LLM: first the full text, then the title, description, keywords,
   and question-answer pairs from the full text.

## Sources

| Source | Use | License |
|---|---|---|
| [OpenEduHub vocabularies](https://github.com/openeduhub/oeh-metadata-vocabs) (`educationalContext`, `new_lrt`, `intendedEndUserRole`, `hochschulfaechersystematik`) | Educational level, content type, target group, higher education subjects | CC0 1.0 |
| Destatis-Systematik der Fächergruppen, Studienbereiche und Studienfächer (Destatis classification of subject groups, study areas, and subjects) | Origin of the higher education subject classification; extracted from the OpenEduHub vocabulary `hochschulfaechersystematik`, not directly from Destatis | “Quelle: Statistisches Bundesamt (Destatis)”, included in the vocabulary (`quellenangabe`) and in the provenance of every seed |
| [OpenAlex Topics](https://github.com/ourresearch/openalex-topic-classification) (4,516 topics with descriptions and keywords) | Higher education topics (specialization/research) | CC0 1.0 |
| [Gemeinsame Normdatei (GND)](https://lobid.org/gnd) via lobid-gnd, [GND-Sachgruppen](https://d-nb.info/standards/vocab/gnd/gnd-sc.html) | GND higher education topics (label, synonyms, broader terms, subject group) | CC0 1.0 |
| [hbz-Verbundkatalog](https://lobid.org/resources) via lobid-resources | Candidates and evidence: frequency of a keyword in textbooks and higher education publications | CC0 1.0 |
| [WirLernenOnline](https://wirlernenonline.de) (editorial team export) | Keywords only, in connection with higher education subjects, as candidates and evidence; no titles, descriptions, or content | Keywords only as frequencies or matched against GND/OpenAlex |
| [KIM-Bildungsstufen](https://w3id.org/kim/educationalLevel/) | `exactMatch` for proficiency levels | CC0 1.0 |
| [ESCO](https://esco.ec.europa.eu) via the public API (2,938 occupations, 12,564 skills, German labels and descriptions) | Vocational education and training occupations and skills, original ESCO URIs | EU reuse (Decision 2011/833/EU), “Quelle: ESCO, Europäische Kommission” in every provenance statement |
| OEH vocabularies `oehTopics` (topic trees of the WLO subject portals, 3,182 topics) and `discipline` | School topics and subjects, subject `closeMatch` for early childhood education | CC0 1.0 |
| OEH vocabularies `kldb` (KldB 2010, 2020 edition, levels 1–4 with short descriptions), `escoSkills`, `dqrCompetenceLevels` | Occupational field (like the WLO field `ccm:oeh_profession_group`), skills matching, DQR levels | CC0 1.0; KldB: “Quelle: Statistik der Bundesagentur für Arbeit” |
| Gemeinsamer Rahmen der Länder für die frühe Bildung in Kindertageseinrichtungen (2022 edition, [according to Eurydice](https://eurydice.eacea.ec.europa.eu/de/eurypedia/germany/leitlinien-der-fruehkindlichen-bildung-betreuung-und-erziehung)) | Seven educational areas for early childhood education | Official work; list and short descriptions in the repository under CC0 |
| Volkshochschul-Statistik des DIE, Fachgebietsgliederung seit 2018 (7 program areas, 85 subject areas): Echarti, Nicolas; Huntemann, Hella; Lux, Thomas; Reichart, Elisabeth (2022): *Volkshochschul-Statistik. 59. Folge, Berichtsjahr 2020.* DIE Survey. Bielefeld: wbv. [DOI 10.3278/70681](https://doi.org/10.3278/70681) | Adult education subjects (codes and labels from the tables, with line breaks joined) | [CC BY 4.0 DE](https://creativecommons.org/licenses/by/4.0/deed.de); full source citation in `programmbereiche.json` and in the provenance of every seed |
| Buhlmann, Rosemarie; Ende, Karin; Kaufmann, Susan; Kilimann, Angela; Schmitz, Helen: *[Rahmencurriculum für Integrationskurse – Deutsch als Zweitsprache](https://www.goethe.de/resources/files/pdf209/rahmencurriculum-integrationskurs_20171.pdf).* Goethe-Institut im Auftrag des BMI (2007), überarbeitete Neuauflage im Auftrag des BAMF (2017) | Language-course topics for adult education (outline) | Copyright protected; only chapter codes and titles have been adopted (capitalization adjusted); full source citation in `sprachkurs_themen.json` and in the provenance of every seed |
| [Umsteigeschlüssel KldB 2020 → ISCO-08](https://statistik.arbeitsagentur.de/DE/Navigation/Grundlagen/Klassifikationen/Klassifikation-der-Berufe/KldB2010-Fassung2020/Arbeitsmittel/Arbeitsmittel-Nav.html) (BA, as of 06.08.2021) | Manual mapping notes for ESCO occupation → KldB and requirement levels | “Quelle: Statistik der Bundesagentur für Arbeit”; temporary cache only |

The vocabularies are prepared and stored under `data/vocabs/` (retrieved with `scripts/fetch_vocabs.py`, date in
the file).

## Higher Education Mappings

**Subjects.** The subjects follow the Destatis-Fächersystematik (source: Statistisches Bundesamt (Destatis)), read
from the OEH version
(`http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/…`): subject group → study area →
field of study. Each topic is assigned one to three fields of study. Study area and subject group are derived from these.

All topics are in one combined set, `themen_hochschule.jsonl` (9.130 topics: 4.514 from OpenAlex,
4.616 from GND). Each topic records its provenance (`art`, `quelle`), status, and evidence; the seeds can
then be filtered accordingly (see “Topic selection”). The partial results for each source are generated during the build in the cache; the `themen` step combines them and runs last after each selection of steps.

**Topics from OpenAlex** (pipeline `openalex`). Each step can be run and resumed independently
(`python -m sem mappings hochschule --liste`):

1. `fachprofile`: The LLM describes each of the 273 fields of study in two sentences, names typical topics,
   and distinguishes it from neighboring fields (`studienfach_profile.jsonl`, also usable on its own).
2. `uebersetzen`: German label, short description, and keywords for each topic. Existing translations
   are retained.
3. `kandidaten`: A local, multilingual embedding model (default `intfloat/multilingual-e5-small`,
   CPU) compares each topic with all subject profiles. Candidates are the 15 most similar fields plus the two most similar fields for each study area in the manual mapping (`openalex_teilgebiet_studienbereich.csv`). This lets the LLM choose from a short, plausible list rather than from 273 fields.
4. `faecher`: three independent votes per topic. Each vote uses a different perspective (subject classification,
   academic advising, library), a different candidate order, and different neighboring topics in the
   batch. For each selected field, the LLM provides a rationale and evidence terms from the topic text.
5. `lehrebene`: also three votes, according to fixed criteria (introductory module, distinct subfield,
   specialist research).
6. `zusammenfuehren`: A field is accepted if it receives at least two of the three votes (up to
   three fields). For each topic, `mapping` contains:
   - `status`: `gesichert` (main field in all votes, usually listed first), `wahrscheinlich`
     (majority), `unsicher` (no majority), or `redaktionell` (corrected),
   - `konfidenz`: share of votes for the main field,
   - for each field: votes, embedding similarity, rationales, and `belegbegriffe_im_text`. These are only
     the evidence terms that actually occur in the topic text; they are checked automatically,
   - `verworfen`: fields with too few votes, `handmapping_bestaetigt`, and the votes for the teaching level.

   Uncertain cases, deviations from the manual mapping, and main fields without evidence in the text are listed in
   `pruefliste_themen.csv`. Corrections entered there (`korrektur_codes` as notations separated by `|`,
   `korrektur_lehrebene`; `-` excludes the topic) and saved as `korrekturen.csv`
   override the LLM result during the next merge. A row containing the existing codes confirms the result. The `korrekturen` step applies the file directly to the completed topic files, without needing the intermediate results (votes, candidates).

   **Review (29.10.2026).** Both review lists (101 OpenAlex topics, 352 GND terms) have been
   editorially reviewed; the results are recorded with comments in `korrekturen.csv`: 101 results confirmed, 249 fields or teaching levels corrected, 103 topics excluded. The exclusions were mainly GND terms that were too general or ambiguous for teaching materials (“Allgemeiner Teil”, “Erweiterung”, “List”), institutions and groups of people (“Universität”, “Student”), as well as two unspecific OpenAlex topics. Corrections often addressed fallback assignments from the embedding (for example, organic chemistry mapped to “Holz-/Fasertechnik”) and topics in subject didactics or higher education teaching.

Seeds inherit `status` and `konfidenz` in the field’s provenance information.

State of the included file (gpt-6-luna, multilingual-e5-small, three votes, 4.516 topics):
before the review, fields: 4.205 gesichert, 297 wahrscheinlich, 14 unsicher; teaching levels: 3.436 gesichert,
1.079 wahrscheinlich, 1 unsicher; manual mapping confirmed for 3.629 topics; 101 topics on the review list.
Six topics received no field in any vote; they were assigned the field with the highest embedding similarity
(`mapping.ersatz`). After the review: 4.514 topics, fields: 4.205 gesichert, 210 wahrscheinlich,
99 redaktionell, no topics still unsicher. GND after the review: 4.616 topics, fields: 4.123 gesichert,
242 wahrscheinlich, 251 redaktionell. Together, the topics cover 256 of the 273 fields of study.

**Topics from GND and the catalog** (pipeline `gnd`). OpenAlex describes research fields;
foundational and classic teaching topics are largely missing there. Additional topics therefore come from the GND:

1. `bruecke`: Each field of study is assigned 1–3 GND subject groups using embedding candidates and three votes
   (`studienfach_gnd_sachgruppe.jsonl`). Subject groups for people (`…p`) and products (`…m`) are
   excluded.
2. `gnd_pool`: Candidates are GND subject headings that frequently appear in the hbz catalog on **textbooks**
   (`natureOfContent` Lehrbuch, at least 3) or **higher education theses** (`type:Thesis`, at least 10),
   queried by notation prefix 000–999, as well as WLO keywords (see below) that **exactly** match the preferred or
   synonym name of a GND subject heading. Each term needs catalog evidence (at least one textbook or three higher education theses); WLO evidence alone is not sufficient.
   Only the GND types subject heading and historical event are allowed; nomenclatures (individual
   substances, species) and motif subject headings (`<Motiv>`) are excluded. People, corporate bodies, companies, places, works, products, and software have their own types in the GND and are therefore excluded. Terms that do not match exactly are discarded.
3. `gnd_relevanz`: three votes on whether the term is a teaching topic in higher education (even if it also occurs in school, e.g. chemistry) or only in school, vocational education and training, everyday life, or not at all (format and media terms such as “Video”, “Vorlesung”). Only terms with
   at least two votes for higher education become topics; the others are listed with votes and rationales in
   `gnd_ausgeschlossen.csv` and can be reused for other education sectors.
4. `gnd_kandidaten`, `gnd_faecher`, `gnd_lehrebene`, `gnd_zusammenfuehren`: as for OpenAlex. Candidate hints come from the bridge and from the fields associated with WLO evidence; the teaching-level step takes the catalog counts into account as a hint. Under `belege` are the catalog counts (titles, textbooks,
   higher education theses) with a query link and, where available, the WLO evidence; under
   `mapping.hochschulrelevanz` are the votes. Uncertain cases are listed in `pruefliste_gnd.csv`.

**WLO keywords** (pipeline `wlo`, option `--wlo <export.csv> …`). From WLO editorial exports, only
keywords, higher education subjects, and, if available, the node ID are read. Higher education subjects are the URIs of the Hochschulfächersystematik in `ccm:taxonid` (which also contains school subjects) and in
`ccm:oeh_taxonid_university`. For each keyword, the count records how many datasets it occurs in with each subject; keywords appearing in at least three datasets are included. The export and these counts remain local
(`data/cache`). Only keywords that exactly match a GND subject heading (GND topics) or a label or keyword of at most three OpenAlex topics are added to the repository (`wlo_belege`), each as evidence with the number of datasets, most frequent subjects, and, where available, links to the nodes. For OpenAlex topics, an additional check determines whether a WLO subject is in the same study area as the approved field
(`belege.wlo.fach_passt`: finest matching level `studienfach`, `studienbereich`, `faechergruppe`, or `nein`; WLO subjects often only specify a broad level), providing an independent cross-check of the subject assignment.

**Content types** (`inhaltstypen.json`): editorial selection of text-capable types from OEH `new_lrt`. For each type, the generated text form, the media format of the full text (text, slides, video or audio transcript), the teaching levels it suits, and optionally the subject groups it applies to are specified
(e.g. an experimental procedure only for natural sciences, engineering, agricultural sciences, and health sciences).

**Levels** (`niveaus.json`): Bachelor’s, 1st–2nd year; Bachelor’s, from 3rd year; Master’s. The mapping to DQR and KIM levels is recorded there. Research topics are excluded from the 1st–2nd year, and foundational topics from Master’s.

## Mappings: Vocational Education and Training

**Topics** are combinations of an ESCO occupation and a skill that ESCO associates with that occupation (essential or optional). ESCO has 2,938 occupations with an average of 18 essential and 16 optional skills; this amounts to around 60,000 essential and 54,000 optional occupation-skill pairs. Occupations and skills carry their original ESCO URIs (`http://data.europa.eu/esco/`…) with German labels and descriptions, unchanged from ESCO.

**Occupational field** is the Classification of Occupations (KldB 2010, 2020 edition) from the OEH vocabulary `kldb`, which WLO uses for `ccm:oeh_profession_group`: Berufsbereich (10) → Berufshauptgruppe (37) → Berufsgruppe (144) → Berufsuntergruppe (703). Each occupation is assigned one or two Berufsuntergruppen; the higher levels are derived.

Steps (`python -m sem mappings berufsbildung --liste`), all resumable:

1. `esco`: Load occupations and all linked skills via the ESCO API (cache `data/cache/esco/`). The API does not return four occupations and five skills due to a server error; these are missing.
2. `hinweise`: The Federal Employment Agency’s crosswalk maps each KldB 5-digit code to ISCO-08 groups. Read in reverse, it gives the matching KldB Berufsuntergruppen and skill levels for each ISCO group (5th digit: helper, skilled worker, specialist, expert). The ISCO code of the ESCO occupation is used to generate a manual mapping hint.
3. `kandidaten`: Local embeddings compare the occupation’s name, synonyms, and description with the labels, definitions, and typical activities of all 703 Berufsuntergruppen. Candidates: the 15 most similar, plus up to four from the crosswalk.
4. `kldb`: Three votes per occupation (three perspectives: KldB coding, career guidance, labor market statistics) select one or two subgroups based on occupational activity, with a rationale and evidence terms.
5. `qualifizierung`: Three votes per occupation on the usual pathway into the occupation in Germany: `ausbildung` (recognized vocational occupation, dual or school-based), `fortbildung` (advanced vocational training following a qualification), `studium` or `anlernen`. The votes are shown the KldB skill levels of the ISCO group as a hint.
6. `wlo` (optional, `--wlo export.csv`): Keywords and occupational groups from WLO datasets with the educational level Berufliche Bildung or Fortbildung. Exact label matches are attached as evidence to skills and occupations (number of datasets, node IDs, KldB occupational groups of the datasets). Titles, descriptions, and URLs are not read; the export remains local.
7. `zusammenfuehren` (run last): Evaluation as for higher education (status, confidence, `handmapping_bestaetigt` = selected subgroup is listed in the crosswalk), `berufe.jsonl`, `kompetenzen.jsonl`, and `pruefliste_berufe.csv`. `korrekturen.csv` takes precedence (columns `korrektur_codes` = KldB codes, `-` = exclude occupation, `korrektur_qualifizierung`).

The skills are matched against the OEH vocabulary `escoSkills` (`skos:exactMatch` to the ESCO URI): 12,323 of 12,539 skills are included there; their OEH URI is given as `exactMatch` in the seed, but the ESCO URI remains authoritative. 438 skills have WLO evidence.

**Editorial review.** All 418 occupations on the review list were reviewed (302 differed from the crosswalk, 99 had no majority for the KldB, 18 had no majority for the qualification pathway). Results are recorded in `korrekturen.csv` with a comment and the original reason from the review list: 327 confirmed, 86 corrected (KldB subgroup and/or qualification pathway, e.g. import/export manager → wholesale and foreign trade clerks, advanced vocational training; air traffic controller → vocational training; food-industry plant occupations from unrelated subgroups), 5 excluded (election observer, polygrafer expert, graphologist, medium, fur farmer), plus the three exclusions from before (escort, fortune-teller, astrologer).

**Result** (2,930 occupations): KldB: 2,104 `gesichert` (confirmed), 413 `wahrscheinlich` (probable), 413 `redaktionell` (editorially reviewed); in 2,039 cases, the crosswalk confirms the selected subgroup. Qualification pathway: vocational training 1,182, advanced vocational training 452, higher education 794, on-the-job training 502 (`gesichert` 2,009, `wahrscheinlich` 508, `redaktionell` 413). The review list is empty. With the default settings (vocational training and advanced vocational training, essential skills without cross-cutting skills, excluding `unsicher`), 33,650 of 113,459 occupation-skill topics are included in the seeds.

**Content types** (`inhaltstypen.json`): 18 types from OEH `new_lrt` with text genres for vocational education and training, e.g. learning situation based on the complete action (as “case study”), work instructions, checklist, examination tasks, training plan based on the four-step method. Each type is restricted to skill types (knowledge or skills) and target groups.

**Levels** (`niveaus.json`) based on the DQR, with `exactMatch` to the OEH vocabulary `dqrCompetenceLevels`: on-the-job training (2), first year of vocational training (3), second and third years of vocational training (4), vocational specialist advanced training (5), Bachelor Professional (6), Master Professional (7). Only what fits the qualification pathway is selected; the educational level follows from it (vocational education and training or advanced vocational training).

## Mappings: Early Childhood Education

**Education sectors** are the seven from the Gemeinsamer Rahmen der Länder for early education in daycare centers (2022 edition, based on Eurydice): integrated language development in everyday activities; personal and socio-emotional development, values, religiosity; STEM; media and digital education; aesthetic education; body, movement, health, prevention; environmental education and education for sustainable development (`bildungsbereiche.json` with short descriptions and `closeMatch` to OEH school subjects used by WLO to index daycare materials).

**Topics** are GND subject headings (`python -m sem mappings elementar --liste`):

1. `kandidaten_llm`: 60–80 typical daycare topics per education sector, generated from three perspectives (early childhood educator, specialist advisor, materials for parents), totaling 1,374 mentions.
2. `wlo` (`--wlo export.csv`): Keywords from WLO datasets with educational level `Elementarbereich` (early childhood education) that occur in at least two datasets (4,122).
3. `gnd`: All 4,626 candidates matched exactly to GND subject headings (lobid), subject headings only: 1,462.
4. `eignung`: Three votes per term: topic for activities with children (`kinderthema`), topic for professionals and parents (`fachthema`), or unsuitable (too specialized, not age-appropriate, unrelated).
5. `bereiche`: Three votes, assigning one or two education sectors to each suitable topic.
6. `alter`: Three votes: under 3 (`u3`), 3 and over (`ue3`), or both age groups.
7. `zusammenfuehren`: `themen_elementar.jsonl`, `pruefliste_themen.csv`; `korrekturen.csv` takes precedence (`korrektur_codes`: education-sector IDs and optionally the topic type, `-` = exclude; `korrektur_alter`).

**Result:** 1,033 topics (874 for activities with children, 159 for professionals and parents); 416 terms rejected as unsuitable. Each education sector has between 51 (media) and 280 (personal and social) topics; the age group is usually “both” (581) or “3 and over” (439), with only 13 explicitly marked “under 3”. Provenance: 664 WLO only, 200 LLM only, 169 both; 833 topics have WLO evidence. The 34 review cases were decided editorially (23 confirmed or corrected, 11 excluded, e.g. methods rather than topics and overly general terms).

**Content types** (`inhaltstypen.json`, 14 from OEH `new_lrt`): activity and project idea, discovery experiment (STEM and environment only), craft instructions, game idea, song/fingerplay/rhyme, read-aloud story, role-play, activity plan, observation form, letter to parents, professional article, instructions, podcast. Each type is restricted by topic type and target group. **Levels**: nursery (under 3), kindergarten (3–6), preschool year (5–6), matched to the age group; `exactMatch` KIM educational level 0.

## Mappings: School

A section for general education schools; the **school level** (primary, lower secondary, upper secondary) is
metadata of the seed (`ccm:educationalcontext`) and can be selected using the `stufen` filter
(e.g. `--auswahl schule.stufen=sek2` for upper-secondary seeds only).

**Topics** are the topic hierarchies of the 29 WLO subject portals (OEH vocabulary `oehTopics`, “Taxonomy of
Curriculum Topics in WirLernenOnline”, CC0): 3,182 topics with subject portal, path, definition, keywords, and
`relatedMatch` to OEH school subjects. WLO indexes materials with these URIs in the `ccm:curriculum` field;
the seeds carry them there as well.

Steps (`python -m sem mappings schule --liste`):

1. `themen`: load topic hierarchies (excluding the subject portals themselves).
2. `wlo` (`--wlo export.csv`): for each topic, the educational levels of the WLO records that carry it in
   `ccm:curriculum` (number of records per level, Node IDs). A level is considered evidenced if it occurs in at
   least 20% of at least three records (2,646 topics).
3. `gnd`: match topic labels exactly to GND subject headings (954 matches), as additional normalization (`gnd`).
4. `art`: three votes: classroom topic, topic for teachers (didactics, methods, school development), or
   aggregation node without its own content (discarded).
5. `stufen`: three votes on which school levels the topic is taught at according to curricula (up to three).
   The LLM does not see the WLO levels; they serve as an independent cross-check (`wlo_bestaetigt`).
6. `zusammenfuehren`: `themen_schule.jsonl`, review list (no majority, discrepancy with WLO, replacement from
   WLO); `korrekturen.csv` takes precedence (`korrektur_codes` = levels, `-` = exclude; `korrektur_art`).

**Result:** 3,153 topics from 29 subject portals (3,030 classroom topics, 123 for teachers); 27 aggregation
nodes discarded. Levels: primary 995, lower secondary 2,763, upper secondary 2,419 topics; status: secured 3,047,
probable 85, editorial 21. In 2,191 cases, the WLO records confirm at least one of the selected levels. The 22
review cases were resolved editorially (OER and school development topics treated as teacher topics for all
levels, pedagogy as an upper-secondary subject, one higher education topic excluded).

**Content types** (`inhaltstypen.json`, 19 from OEH `new_lrt`): e.g. worksheet, practice material,
explainer video transcript, textbook chapter, fact sheet, blackboard diagram, class test, marking scheme, quiz,
learning path, station-based learning, experiment instructions (natural sciences and geography only), lesson plan,
teaching unit, subject article for teachers; each type has a topic type, target audience, levels, and subject
portals.
**Levels**: grades 1–2, 3–4, 5–6, 7–8, 9–10, introductory and qualification phases, `exactMatch`
KIM educational levels 1–3.

## Mappings: Adult Education

General and vocational continuing education in course format (adult education centers and others); advanced
vocational training (master craftsperson, specialist business administrator) belongs to vocational education and
training.

**Subject areas** are the 7 program areas and 85 subject areas of the DIE Volkshochschul-Statistik
(Volkshochschul statistics; subject area classification since reporting year 2018; `programmbereiche.json`), e.g.
1.04 Global Learning/ESD, 3.05 Food and Drink, 4.06 English, 5.04 Commercial Courses, 6.01 Lower Secondary
School Leaving Certificate, 7.01 Literacy. The aggregate categories x.00 are not assignment targets. The 24
foreign languages are grouped into one shared target, “4.fs”, for topics, because language course topics apply
to all languages; the specific language is selected when the seed is drawn (rotating by topic) and added as the
target language in `cclom:general_language`.

**Topics** from three sources (filter `quellen`):

1. `gnd` (955): steps `kandidaten_llm` (30–40 typical course topics per subject area, from two perspectives),
   `gnd` (exact match to GND subject headings, 1,076 matches), `kandidaten` (the six most similar subject areas
   per term using embeddings, plus the subject areas that suggested it), `eignung` (three votes: course topic,
   topic for course instructors, unsuitable; 121 discarded), `fachgebiete` (three votes, one or two subject
   areas). WLO keywords for adult education serve only as evidence (`wlo`, 347 topics), not as candidates,
   because they are heavily mixed with other sectors.
2. `rahmencurriculum` (117): language course topics from the outline of the Rahmencurriculum für
   Integrationskurse (Framework Curriculum for Integration Courses; Goethe-Institut on behalf of the BMI,
   2017): the twelve areas of action with their learning objective areas (e.g. “Housing: understanding and
   signing rental agreements”) and the five cross-cutting areas (`sprachkurs_themen.json`; only codes and titles
   were carried over, with capitalization restored from all-caps by the LLM). All apply to German as a foreign
   language; everyday topics also apply to foreign languages. GND terms rarely capture such communication
   situations.
3. `schulthemen` (485): for school-leaving qualification courses, classroom topics from the School section in
   German, mathematics, and English (lower secondary → Hauptschulabschluss and Realschulabschluss, upper
   secondary → Fachhochschulreife and Abitur).

**Result:** 1,557 topics in 54 subject areas (program areas: Politics–Society–Environment 323,
Culture–Creative Activities 308, Health 103, Languages 146, Working Life–IT–Organization 141,
School-Leaving Qualifications 505, Basic Education 31). The 19 review cases were resolved editorially.
Basic Education has few topics; with even distribution by program area, they recur.

**Content types** (14 from OEH `new_lrt`): course description, handout, exercise sheet, instructions, checklist,
self-study unit, presentation, quiz, vocabulary list and dialogue/role-play (Languages, Basic Education),
practice exam (Languages, School-Leaving Qualifications), podcast, course schedule, and subject article for
course instructors.
**Levels** by program area: CEFR A1–C1 (Languages), Alpha Levels 1–2 and 3–4 (Basic Education), preparation
for the Hauptschulabschluss, Realschulabschluss, or higher education entrance qualification
(School-Leaving Qualifications), otherwise beginner and advanced.

## Seeds

A seed is a JSON object (one line in JSONL). Field names follow the edu-sharing/OEH schema:

| Field | Content | Origin |
|---|---|---|
| `ccm:educationalcontext` (+ `_DISPLAYNAME`) | Educational level, e.g. higher education | hard-coded per sector |
| `ccm:oeh_taxonid_university` (+ `_DISPLAYNAME`) | 1–3 fields of study | from the topic |
| `ccm:oeh_lrt` (+ `_DISPLAYNAME`) | Content type | appropriate to the teaching level, rotated |
| `ccm:educationalintendedenduserrole` (+ `_DISPLAYNAME`) | Target group | weighted (85% learners, 15% teachers) |
| `cclom:general_language` | Language | fixed as `de` |
| `synth:thema` | Topic with URI, labels, description, keywords, teaching level, field of study, subject group | mapping |
| `synth:niveau` | Level | evenly distributed (with exclusions) |
| `synth:textsorte`, `synth:medienform` | Generation controls | from the content type |
| `synth:didaktische_funktion` | e.g. introduction, practice, and application | in 60% of seeds, random |
| `synth:bearbeitungsdauer_minuten` | 15, 30, 45, or 90 | in 50% of seeds, random |
| `synth:provenance` | Provenance for each field: source, URI, license, method | automatic |
| `synth:seed_parameter` | Even-distribution field, group, random seed, topic selection | automatic |

Controlled values are stored as a list of URIs, as in edu-sharing. The label is stored in `<field>_DISPLAYNAME` and is taken unchanged from the vocabulary. Labels are cleaned up for prompts (without the prefix “Studienbereich” and without administrative additions in parentheses).

**Even distribution.** For each education sector, one field is selected (higher education: `thema` by default, as well as `studienfach`, `studienbereich`, `faechergruppe`, and `lehrebene`). Topics are grouped by this field, using the first value in each case. The requested number is distributed evenly across the groups, with the remainder assigned to randomly selected groups. Within a group, topics are placed in random order, each appearing once before any topic is repeated. For `n` ≥ the number of topics, every topic is therefore included when using `thema`. The content type is rotated within each group: the matching type used least often so far is selected each time.

**Topic selection.** Which topics are included in the seeds can be selected (CLI `--auswahl`, user interface “welche Themen einbezogen werden” (“which topics are included”); `python -m sem auswahl hochschule` shows the filters and topic count):

| Filter | Values | Default |
|---|---|---|
| `quellen` | `openalex`, `gnd` | both |
| `fach_status` | `gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell` | excluding `unsicher` |
| `lehrebene_status` | same as `fach_status` | excluding `unsicher` |
| `hochschulrelevanz` (GND only) | `gesichert`, `wahrscheinlich`, `unsicher` | excluding `unsicher` |
| `belege` (at least one) | `lehrbuch` (≥ 1 textbook in the hbz catalog), `hochschulschrift` (≥ 3), `wlo` (WLO keyword with a matching higher-education subject), `handmapping` (subject confirmed through hand mapping or a subject-group bridge) | no requirement |

`unsicher` is excluded by default until the review list has been reviewed; a correction or confirmation makes a topic `redaktionell`, so it is included again. Once the review is complete, all 9.130 topics are included by default.
Each seed records in `synth:provenance` → `synth:thema` the status of each assignment and its evidence: catalog counts with query URIs (lobid), WLO records with a reference to WirLernenOnline.de and, where included in the export, links to the Node IDs. Only WLO keywords associated with higher-education subjects are used. The `.stats.json` counts topic sources, statuses, and evidence.

**Vocational education and training seeds.** Instead of `ccm:oeh_taxonid_university`, they include `ccm:oeh_profession_group` (KldB occupational subgroups, OEH URIs), as well as `synth:beruf` (ESCO URI, label, ISCO-08, description, qualification pathway, higher-level KldB categories) and `synth:kompetenz` (ESCO URI, label, description, type, scope, essential/optional, `exactMatch` to OEH `escoSkills`). `synth:thema` is the competency in the context of the occupation. The content type matches the competency type and target group (80% learners, 20% teachers and trainers); the level matches the qualification pathway. Optional: `synth:lernort` (40%; vocational school, training company, inter-company training center, or continuing education venue) and `synth:querschnittsthema` (25%; one of the four standard occupational profile positions). Even distribution: `beruf` (default), `thema`, `kompetenz`, `berufsgruppe`, `berufshauptgruppe`, `berufsbereich`.

| Filter (vocational education and training) | Values | Default |
|---|---|---|
| `qualifizierung` | `ausbildung`, `fortbildung`, `studium`, `anlernen` | Ausbildung and Fortbildung |
| `relation` | `essenziell`, `optional` | essenziell |
| `kompetenzart` | `wissen`, `faehigkeit` | both |
| `reichweite` | `berufsspezifisch`, `branchenspezifisch`, `branchenuebergreifend`, `querschnitt` | excluding `querschnitt` |
| `kldb_status`, `qualifizierung_status` | same as `fach_status` | excluding `unsicher` |
| `belege` | `wlo` (competency as a WLO keyword with records from the same KldB occupational group), `wlo_beruf` (occupation as a WLO keyword), `handmapping` (KldB confirmed through a crosswalk) | no requirement |

The German full-text prompt for vocational education and training (`prompts/berufsbildung/volltext.md`) names the occupation, competency, occupational field, learning venue, and cross-cutting topic, and requires a connection to typical work tasks in the occupation.

**Early childhood education seeds.** Educational level is fixed as early childhood education; `synth:bildungsbereich_elementar` (education sector from the Gemeinsamer Rahmen der Länder (Common Framework of the Länder)) and `ccm:taxonid` (`closeMatch` to OEH school subjects). Target group by topic type: child-focused topics—65% professionals, 20% parents, 15% children (read-aloud story, song); professional topics—75% professionals, 25% parents. Optional: didactic function, duration, social format (40% each). Even distribution: `bildungsbereich` (default), `thema`, `altersgruppe`, `art`. Filters: `art`, `herkunft` (`llm`, `wlo`), `eignung_status`, `bereich_status`, `alter_status`, `belege` (`wlo`, `llm_bestaetigt`). A dedicated German full-text prompt (`prompts/elementar/volltext.md`) is age-appropriate, avoids tests and grades, and includes guidance on safety and diversity.

**School seeds.** `ccm:educationalcontext` is the school level: for each topic, a level is selected in rotation from its levels (within the `stufen` filter); the level (year group) matches it. `ccm:taxonid` = school subject from the topic tree, `ccm:curriculum` = topic in the WLO topic tree. Foreign-language portals add the target language to `cclom:general_language`. Target group: classroom topics—75% learners, 25% teachers; teacher-focused topics—teachers only. Optional: lesson phase (60%), duration (50%), KMK competency level I–III (40%, only I and II in primary education), social format (30%). Even distribution: `thema` (default), `portal` (small portals often repeat their topics; “Gewaltprävention” has only one), `fach`, `stufe`. Filters: `stufen`, `art`, `stufen_status`, `art_status`, `belege` (`wlo`, `wlo_stufe`, `gnd`). A dedicated German full-text prompt (`prompts/schule/volltext.md`) includes the topic path, year group, operators, and target language.

**Adult education seeds.** Educational level is fixed as adult education; `synth:fachgebiet_vhs` and `synth:programmbereich_vhs` (Volkshochschul-Statistik (adult education center statistics)). Target group: course topics—80% participants, 20% course instructors; topics for course instructors—course instructors only. For basic education always, otherwise in 5% of seeds, `synth:sprachniveau` = simple language. Optional: use (50%), duration (40%), event format (40%; evening course, weekend seminar, educational leave, online course, lecture, self-study offering). Even distribution: `programmbereich` (default), `fachgebiet`, `thema`, `art`. Filters: `programmbereiche`, `quellen`, `art`, `eignung_status`, `fachgebiet_status`, `belege` (`wlo`, `llm_bestaetigt`). A dedicated German full-text prompt (`prompts/erwachsenenbildung/volltext.md`) includes the target language and simple language.

**Reproducibility.** The same mappings, settings, and random seed produce identical seeds. A `.stats.json` file containing parameters and distributions is stored alongside each seed file.

## Materials

Up to six LLM calls per seed (OpenAI-compatible API, default model `gpt-6-luna`):

1. **Full text** from the seed: `volltext_system.md` and `volltext.md`, about one A4 page
   (400–550 words). Shorter texts are discarded and requested again.
2. **Title, description, keywords**, one call each, in that order. All use the same system prompt
   (`system.md`) and the same material template (`material.md`): full text, the seed’s fixed
   metadata (educational level, subject, target audience, language, material type), and the fields
   already generated; empty fields are shown as “-”. The field file (`titel.md`, `beschreibung.md`,
   `keywords.md`) provides the instructions. The LLM does not see the topic, topic description, or
   seed keywords here; the fields are therefore derived from the text, as a model is later expected
   to learn “text → metadata”. Responses are validated (title 10–120 characters,
   description 250–1,200, at least 4 keywords, at most 8 retained); an unusable field is requested
   again up to three times, without generating the full text again.
3. **Content-type check** (`inhaltstyp.md`, can be disabled): The LLM determines the material type
   from the completed material without seeing the seed value, and selects from the sector’s content
   types. The result is recorded in `synth:inhaltstyp_pruefung` (expected, detected, match) and serves
   as a quality measure; `ccm:oeh_lrt` remains the seed value.
4. **Question-answer pairs** (`fragen_antworten.md`, JSON): Target 10 pairs with a learning objective
   level according to Bloom/Anderson, minimum 8. Steps 3 and 4 run simultaneously.

Each material records in `synth:generierung` the duration and which fields had to be requested again
(`neu_angefragt`). The progress display also counts retries of whole materials (e.g. after a rate
limit or timeout) and shows the latest message. A material needs five consecutive calls; with
reasoning models, a lower reasoning effort (`--reasoning-effort`, interface “Reasoning effort”)
shortens the run time considerably.

The prompts for the title, description, keywords, and content type, as well as the shared system
prompt, are in German, adapted from WLO metadata enrichment for synthetic materials.

Each material retains all seed fields and gets `synth:volltext` and `synth:generierung` (model,
Base URL, date, hash of the prompts used).

**Concurrency, retries, resuming.** The default is 20 workers. Failed calls are retried with
increasing wait times (6 attempts by default). If the API is unreachable for longer than the
configured time (10 minutes by default), the run pauses in an orderly manner. Results are written
immediately, one line at a time. Restarting with the same output file skips everything completed and
retries failed items (`<ausgabe>.fehler.jsonl`). A lock file prevents two simultaneous runs on the
same file.

## Placeholders

In the prompt templates (`prompts/`, editable in the user interface), placeholders use the form
`{{name}}`. Conditional sections `{{#name}}…{{/name}}` appear only when the value is set.

| Placeholder | Content |
|---|---|
| `textsorte`, `inhaltstyp`, `medienform` | from the content type |
| `thema`, `thema_beschreibung`, `thema_keywords` | from the topic (in German) |
| `fach`, `bildungsstufe`, `niveau`, `zielgruppe` | from the metadata (cleaned labels) |
| `didaktische_funktion`, `bearbeitungsdauer` | optional |
| `beruf`, `beruf_beschreibung`, `kompetenzart`, `lernort`, `querschnittsthema` | vocational education and training only (`lernort`, `querschnittsthema` optional) |
| `themenart`, `sozialform` | early childhood education (`sozialform` optional, also school) |
| `thema_pfad`, `zielsprache`, `anforderungsbereich` | school only (`anforderungsbereich` optional, `zielsprache` only for foreign languages) |
| `programmbereich`, `einfache_sprache`, `veranstaltungsform` | adult education only (also `zielsprache`) |
| `rolle`, `sprache` | author role in the full-text prompt, language as a word |
| `titel`, `beschreibung`, `keywords`, `volltext`, `auftrag` | only in `material.md`: fields generated so far (“-” = empty), full text, instructions from the field file |
| `kategorien`, `beispiel_id` | only in `inhaltstyp.md`: list of content types in “Name → id” format |

## Known limitations

- OpenAlex topics come from research literature. Medicine, biology, and engineering are strongly
  represented, while philology, art, and music are weakly represented. The GND topics and even
  distribution by subject partly compensate for this.
- LLM assignments are non-deterministic. Multiple votes make the variation visible
  (status and confidence), but do not eliminate it. Three votes from the same model can all be
  wrong; embedding similarity and manual mapping serve as independent cross-checks.
- The teaching levels are unevenly distributed (94 `grundlagen`, 2,810 `vertiefung`, 1,612 `forschung`). Since research
  topics are excluded in the first–second year of study, this level is less common when distributing
  evenly by topic (around 22% rather than 33% for 30,000 seeds). GND topics from textbooks help
  compensate for this.
- ESCO is European. Many occupations do not correspond one-to-one to a German training occupation,
  and some German translations are awkward. The qualification pathway is an estimate based on
  voting, not an official assignment to recognized training occupations (their directory is not
  openly licensed). Learning fields from the framework curricula are not available in machine-readable
  form; they are missing.
- Synthetic texts may contain factual errors. For training purposes, mapping text →
  metadata is more important than subject-matter depth. They have not been reviewed for publication
  as learning materials.

## Create a new education sector

1. Create `sem/bereiche/<bereich>/__init__.py` with a class that implements `Bildungsbereich`
   (`topics`, `balance_key`, `make_seed`, `prompt_values`), and register it in
   `sem/bereiche/__init__.py`. Optional: `auswahl_optionen` and `passt` for selectable filters (status, sources,
   evidence), `inhaltstyp_kategorien` for the content-type check.
2. Add mappings under `data/mappings/<bereich>/`, using the same topic structure as for higher education
   (`id`, `label_de`, subject fields with `uri`/`label`, `quelle`, `mapping`).
3. To build the mappings, set `mapping_schritte()` and `mapping_folge`. The CLI and user interface will then
   display the steps automatically. The components in `sem/mapping/` are sector-independent: embedding
   candidates (`embeddings.py`), multiple votes with evidence (`abstimmung.py`), review list and
   corrections (`pruefung.py`). A school sector might vote on mappings from topics to school subjects, while a
   vocational education and training sector might vote on mappings from ESCO competencies to occupations.
4. Optionally, add custom prompts under `prompts/<bereich>/`.
