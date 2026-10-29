# Prompts

Alle Prompts sind editierbar, entweder hier als Datei oder in der Oberfläche (Reiter „Prompts“).
Platzhalter stehen in doppelten geschweiften Klammern, z. B. `{{thema}}`. Welche es gibt, steht in
[`../METHODIK.md`](../METHODIK.md#platzhalter).

| Datei | Schritt | Ausgabe |
|---|---|---|
| `volltext_system.md` | Volltext erzeugen (Rolle) | – |
| `volltext.md` | Volltext erzeugen (Auftrag aus dem Seed) | Fließtext/Markdown, etwa eine A4-Seite |
| `metadaten_system.md` | Metadaten erzeugen (Rolle, Ausgabeformat) | JSON |
| `titel.md` | Vorgaben für den Titel | Feld `titel` |
| `beschreibung.md` | Vorgaben für den Beschreibungstext | Feld `beschreibung` |
| `keywords.md` | Vorgaben für die Keywords | Feld `keywords` |
| `fragen_antworten.md` | Vorgaben für die Frage-Antwort-Paare | Feld `frage_antwort_paare` |

Die vier Metadaten-Vorgaben werden zu einer Anfrage zusammengesetzt. Sie sehen **nur den Volltext**
(plus Inhaltstyp), nicht den Seed: So lassen sich Titel, Beschreibung und Keywords tatsächlich aus
dem Text ableiten, und ein später trainiertes Modell lernt genau diese Abbildung.

Ein Bildungsbereich kann eigene Fassungen mitbringen: `prompts/<bereich>/<datei>` hat Vorrang.
