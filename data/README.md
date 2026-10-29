# Daten

## `vocabs/`

Je Vokabular eine Datei `<name>.json`:

```json
{"scheme": {"name", "uri", "title", "source", "public_url", "license", "retrieved"},
 "concepts": [{"uri", "notation", "label", "broader", "level"}]}
```

`broader` ist die URI des Oberbegriffs, `level` die Tiefe (0 = oberste Ebene). Neu laden mit
`python scripts/fetch_vocabs.py`.

## `mappings/hochschule/`

| Datei | Inhalt |
|---|---|
| `themen_openalex.jsonl` | ein Thema je Zeile, siehe unten |
| `themen_grundlagen.jsonl` | Grundlagenthemen, gleiche Struktur, zusätzlich `gnd` (GND-ID, Sachgruppe, Oberbegriff) |
| `grundlagen_ohne_gnd.csv` | LLM-Vorschläge ohne GND-Treffer (nicht verwendet) |
| `openalex_teilgebiet_studienbereich.csv` | Handmapping OpenAlex-Teilgebiet → Studienbereich(e) |
| `inhaltstypen.json` | genutzte Inhaltstypen aus OEH `new_lrt` mit Textsorte, Medienform, Lehrebenen |
| `niveaus.json` | Niveaustufen innerhalb der Hochschule |
| `optionale_felder.json` | Werte und Anteile der optionalen Steuerfelder |

Themen-Struktur:

```json
{"id": "https://openalex.org/T10001", "art": "openalex | grundlagen",
 "label_de": "…", "label_en": "…", "beschreibung_de": "…", "keywords_de": ["…"], "keywords_en": ["…"],
 "studienfach":   [{"uri": "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/n065", "notation": "065", "label": "…"}],
 "studienbereich": [{"uri", "notation", "label"}], "faechergruppe": [{"uri", "notation", "label"}],
 "lehrebene": "grundlagen | vertiefung | forschung",
 "quelle":  {"name", "uri", "lizenz"},
 "mapping": {"methode", "modell", "datum", "prompt_hash"}}
```
