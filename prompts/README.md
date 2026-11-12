# Prompts

*English · [Deutsch](README.de.md)*

All prompts are editable, either here as files or in the user interface (tab “Prompts”). The prompt templates themselves are in German.
Placeholders use double curly braces, e.g. `{{thema}}`. See
[`../METHODOLOGY.md`](../METHODOLOGY.md#placeholders) for the available placeholders.

| File | Step | Output |
|---|---|---|
| `volltext_system.md` | Generate full text (role, basic rules) | – |
| `volltext.md` | Generate full text (instructions from the seed, task) | Prose/Markdown, length per content type (`{{umfang_von}}`–`{{umfang_bis}}` words) |
| `system.md` | Shared system prompt for all metadata fields | – |
| `material.md` | Shared template: full text, existing fields, `{{auftrag}}` (full text first so the provider can cache the unchanged beginning) | – |
| `titel.md` | Title task | one line |
| `beschreibung.md` | Description task | text, 4–6 sentences, 450–900 characters |
| `keywords.md` | Keywords task | comma-separated list, at least 5, at most 8 kept |
| `inhaltstyp.md` | Content type verification task (without seed value) | one ID from `{{kategorien}}` (with text-type description) |
| `fragen_antworten.md` | Question-and-answer pairs task | JSON `{"paare": [...]}` |
| `beschreibung_einstieg.md` | only the `beschreibung-einstieg` command: replace the first sentence of a finished description | one sentence |
| `ausgabesprache.md` | Note for both system prompts, only when an output language other than German is selected during generation (app: “Ausgabesprache” (output language), CLI: `--sprache en`) | – |

The fields are generated sequentially: Title, Description, Keywords, Content type, Questions. Each call
inserts the field file text as `{{auftrag}}` in `material.md` and sees the fields generated previously.
The topic from the seed is visible only to the full-text prompt; the metadata is derived from the text.

Title, Description, Keywords, Content type, and the system prompt follow the prompts for WLO metadata enrichment (with examples and a terminology note for higher education added).

An education sector can include its own versions: `prompts/<bereich>/<datei>` takes precedence.

Notes and improvement suggestions for these prompts (feedback, not active templates):
[`PROMPT_FEEDBACK.md`](PROMPT_FEEDBACK.md).
