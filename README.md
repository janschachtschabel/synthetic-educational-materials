# synthetic-educational-materials

*English · [Deutsch](README.de.md)*

Builds **synthetic German educational materials** as training data for metadata models. Each material
has a full text of about one A4 page plus the metadata a repository like
[WirLernenOnline](https://wirlernenonline.de) needs: title, description, keywords, content type and didactic
question-answer pairs. Structure and field names follow the OpenEduHub/edu-sharing metadata schema.

```
open vocabularies ──► topics per subject ──► seeds ──► full text ──► title, description, keywords, Q&A
(OEH, Destatis, ESCO,    (mappings, once)    (no LLM)   (LLM)        (LLM, derived from the full text only)
 GND, OpenAlex, KMK …)
```

Five education sectors are separate modules: **school**, **higher education**, **vocational education and training**,
**early childhood education** and **adult education**. Further sectors can be added.

**Results:** the dataset [synthetic-german-educational-materials-50k](https://huggingface.co/datasets/JanSchachtschabel/synthetic-german-educational-materials-50k)
(50,000 materials across all five sectors) and an mT5-small model trained on it that generates title,
description and keywords from a full text, also on CPU ([training/](training/README.md)).

## Principles

1. **Open sources, traceable values.** Topics, subjects, levels and content types come from open vocabularies and
   taxonomies. Every value carries URI, label and its provenance (`synth:provenance`), including the source's
   attribution where required.
2. **No copied content.** No text from existing educational materials is taken over. Real repository data
   (WLO, OERSI) is only used in aggregate: keyword × subject combinations as evidence for topics, normalized to GND
   subject headings, never as text.
3. **Metadata from the text.** Only the full-text step sees the seed's topic. Title, description and keywords are
   then derived from the full text alone, which is exactly the task a model later has to learn.
4. **Measured confidence instead of guessing.** Mappings combine local embeddings with several LLM votes. Each
   mapping has a status (`gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell`), evidence and a review list;
   manual corrections take precedence. Seeds can be filtered by status, source and evidence.
5. **Balanced and reproducible.** Seeds are drawn without an LLM, spread evenly over subjects or topics, with a
   fixed random seed. Prompts are recorded by hash, local data by fingerprint, and every run can be resumed.
6. **Copyright respected.** Protected sources (e.g. framework curricula) are only used locally under `data/lokal/`
   and only as chapter codes and titles. Everything runs without them.
7. **Checked output.** Every LLM answer is validated (length, format, forbidden characters, readable titles instead
   of keyword chains) and requested again if it fails.

## Quick start

Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) recommended. Set `OPENAI_API_KEY` (or `LLM_ANBIETER=anthropic`
and `ANTHROPIC_API_KEY`) in the environment, then:

```
uv sync --all-extras
uv run python app.py             # web interface on http://127.0.0.1:8501
```

Or from the command line:

```
uv run python -m sem seeds --anzahl schule=1000 --seed 42
uv run python -m sem materialien --seeds output/seeds_schule1000_s42.jsonl --limit 50
```

## Documentation

| Document | Contents |
|---|---|
| [docs/usage.md](docs/usage.md) | installation, web interface, command line, Flex and batch, resuming, data files |
| [METHODOLOGY.md](METHODOLOGY.md) | sources and licenses, how mappings, seeds and materials are created, known limitations |
| [data/README.md](data/README.md) | vocabularies and mapping files in detail |
| [prompts/README.md](prompts/README.md) | the prompt templates |
| [training/README.md](training/README.md) | Hugging Face export and mT5 fine-tuning; measured numbers in [TRAINING_NOTES.md](training/TRAINING_NOTES.md) |

## License

Code: Apache 2.0. Data under `data/`: derived from CC0 sources; ESCO labels and descriptions are subject to
the European Commission's reuse conditions (source: ESCO, European Commission), KldB with
attribution to the Bundesagentur für Arbeit, Volkshochschul-Statistik codes and labels from the DIE statistics
report (CC BY 4.0 DE), only chapter codes and titles from the Rahmencurriculum für Integrationskurse (copyrighted,
cited in every provenance entry). See METHODOLOGY.md for provenance.
