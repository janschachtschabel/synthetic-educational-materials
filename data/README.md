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
| `studienfach_profile.jsonl` | je Studienfach Kurzbeschreibung, typische Themen, Abgrenzung (LLM) |
| `pruefliste_themen.csv` | Zuordnungen mit Grund für eine Durchsicht, leere Korrekturspalten |
| `korrekturen.csv` | optional: redaktionelle Korrekturen (Spalten `id`, `korrektur_codes`, `korrektur_lehrebene`, `kommentar`), Vorrang vor dem LLM |
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
 "mapping": {"methode", "modell", "embedding_modell", "stimmen", "datum", "prompt_hash",
             "status": "gesichert | wahrscheinlich | unsicher | redaktionell", "konfidenz": 1.0,
             "handmapping_bestaetigt": true, "kandidaten": 17,
             "faecher": [{"uri", "notation", "label", "stimmen": 3, "an_erster_stelle": 3, "konfidenz": 1.0,
                          "aehnlichkeit": 0.84, "begruendungen": ["…"], "belegbegriffe_im_text": ["…"]}],
             "verworfen": {"Fach": 1},
             "lehrebene": {"status", "konfidenz", "stimmen": {"grundlagen": 0, "vertiefung": 2, "forschung": 1},
                           "begruendungen": ["…"]}}}
```
