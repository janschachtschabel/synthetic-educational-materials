# Prompts

*[English](README.md) · Deutsch*

Alle Prompts sind editierbar, entweder hier als Datei oder in der Oberfläche (Reiter „Prompts“).
Platzhalter stehen in doppelten geschweiften Klammern, z. B. `{{thema}}`. Welche es gibt, steht in
[`../METHODIK.md`](../METHODIK.md#platzhalter).

| Datei | Schritt | Ausgabe |
|---|---|---|
| `volltext_system.md` | Volltext erzeugen (Rolle, Grundregeln) | – |
| `volltext.md` | Volltext erzeugen (Vorgaben aus dem Seed, Auftrag) | Fließtext/Markdown, etwa eine A4-Seite |
| `system.md` | gemeinsamer Systemprompt für alle Metadatenfelder | – |
| `material.md` | gemeinsame Vorlage: Volltext, vorhandene Felder, `{{auftrag}}` (Volltext zuerst, damit der Anbieter den gleichbleibenden Anfang zwischenspeichern kann) | – |
| `titel.md` | Auftrag Titel | eine Zeile |
| `beschreibung.md` | Auftrag Beschreibung | Text, 400–800 Zeichen |
| `keywords.md` | Auftrag Schlagworte | kommagetrennte Liste, 5–8 |
| `inhaltstyp.md` | Auftrag Inhaltstyp-Prüfung (ohne Seed-Wert) | eine id aus `{{kategorien}}` |
| `fragen_antworten.md` | Auftrag Frage-Antwort-Paare | JSON `{"paare": [...]}` |
| `ausgabesprache.md` | Hinweis an beide Systemprompts, nur wenn beim Erzeugen eine andere Ausgabesprache als Deutsch gewählt ist (App: „Ausgabesprache“, CLI: `--sprache en`) | – |

Die Felder werden nacheinander erzeugt: Titel, Beschreibung, Keywords, Inhaltstyp, Fragen. Jeder Aufruf
setzt den Felddatei-Text als `{{auftrag}}` in `material.md` ein und sieht die zuvor erzeugten Felder.
Das Thema aus dem Seed sieht nur der Volltext-Prompt; die Metadaten werden aus dem Text abgeleitet.

Titel, Beschreibung, Schlagworte, Inhaltstyp und der Systemprompt folgen den Prompts der
WLO-Metadatenanreicherung (Beispiele und Terminologie-Hinweis für die Hochschule ergänzt).

Ein Bildungsbereich kann eigene Fassungen mitbringen: `prompts/<bereich>/<datei>` hat Vorrang.
