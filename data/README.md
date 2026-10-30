# Daten

## `vocabs/`

Je Vokabular eine Datei `<name>.json`:

```json
{"scheme": {"name", "uri", "title", "source", "public_url", "license", "retrieved"},
 "concepts": [{"uri", "notation", "label", "broader", "level"}]}
```

`broader` ist die URI des Oberbegriffs, `level` die Tiefe (0 = oberste Ebene). Wo das Vokabular sie hat,
stehen zusätzlich `definition` und `note` (KldB: Kurzbeschreibung und typische Tätigkeiten). Neu laden mit
`python scripts/fetch_vocabs.py` (alle) oder `python scripts/fetch_vocabs.py kldb` (einzelne).

## `mappings/berufsbildung/`

| Datei | Inhalt |
|---|---|
| `berufe.jsonl` | ein ESCO-Beruf je Zeile, siehe unten |
| `kompetenzen.jsonl` | eine ESCO-Kompetenz je Zeile: `uri`, `label`, `alt_labels`, `beschreibung`, `art` (`wissen`, `faehigkeit`), `reichweite` (`berufsspezifisch`, `branchenspezifisch`, `branchenuebergreifend`, `querschnitt`), `isced_f`, `essenziell_fuer`/`optional_fuer` (Zahl der Berufe), `oeh_uri` (OEH escoSkills), `belege.wlo` |
| `pruefliste_berufe.csv` | Berufe mit Grund für eine Durchsicht, leere Korrekturspalten |
| `korrekturen.csv` | redaktionelle Korrekturen (`korrektur_codes` = KldB-Kennziffern mit `|`, `-` = ausschließen, `korrektur_qualifizierung`, `kommentar`), Vorrang vor dem LLM |
| `inhaltstypen.json` | genutzte Inhaltstypen aus OEH `new_lrt` mit Textsorte, Medienform, Kompetenzarten, Zielgruppen |
| `niveaus.json` | DQR-Niveaus mit Qualifizierungsweg und Bildungsstufe |
| `optionale_felder.json` | Werte und Anteile der optionalen Steuerfelder (Lernort, Querschnittsthema …) |

Beruf-Struktur:

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

Die Themen (Beruf × Kompetenz) entstehen beim Laden aus beiden Dateien und werden nicht gespeichert.

## `mappings/hochschule/`

| Datei | Inhalt |
|---|---|
| `themen_hochschule.jsonl` | Gesamtset: alle Themen, ein Thema je Zeile, siehe unten. `art` = `openalex` oder `gnd`; GND-Themen haben zusätzlich `gnd` (GND-ID, Typ, Sachgruppe, Oberbegriffe), `belege.katalog` und `mapping.hochschulrelevanz`; Themen mit WLO-Beleg `belege.wlo`. Die Teilergebnisse je Quelle entstehen beim Bauen im Zwischenspeicher (`data/cache/hochschule_themen_*.jsonl`) und werden im Schritt `themen` zusammengeführt |
| `studienfach_profile.jsonl` | je Studienfach Kurzbeschreibung, typische Themen, Abgrenzung (LLM) |
| `pruefliste_themen.csv` | Zuordnungen mit Grund für eine Durchsicht, leere Korrekturspalten |
| `korrekturen.csv` | redaktionelle Durchsicht beider Prüflisten (Spalten `id`, `label_de`, `korrektur_codes` mit `|`, `-` = ausschließen, `korrektur_lehrebene`, `kommentar`, `prueflistengrund`), Vorrang vor dem LLM |
| `pruefliste_gnd.csv` | GND-Themen, die eine Durchsicht brauchen |
| `gnd_ausgeschlossen.csv` | GND-Begriffe, die nicht als Hochschul-Lehrinhalt eingestuft wurden (mit Bereich und Stimmen) |
| `studienfach_gnd_sachgruppe.jsonl` | Brücke Studienfach → GND-Sachgruppen mit Stimmen |
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
