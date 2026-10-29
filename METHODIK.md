# Methodik

Kurzbeschreibung, wie Seeds und synthetische Materialien entstehen, damit Ergebnisse nachvollziehbar
und reproduzierbar sind. Jeder Datensatz trägt zusätzlich unter `synth:provenance` die Herkunft
jedes Feldes.

## Überblick

```
offene Vokabulare und Taxonomien ──► Mappings (Themen mit Fachzuordnung) ──► Seeds ──► Materialien
     (OEH, Destatis, OpenAlex, GND)      data/mappings/<bereich>/            ohne LLM     mit LLM
```

1. **Mappings** ordnen Themen aus offenen Quellen den Fächern eines Bildungsbereichs zu. Sie werden
   einmal gebaut, liegen im Repo und sind auch für sich nutzbar.
2. **Seeds** kombinieren je ein Thema mit fest codierten und gezogenen Metadaten (Bildungsstufe, Fach,
   Inhaltstyp, Niveau …). Kein LLM, reproduzierbar über einen Zufalls-Seed.
3. **Materialien** entstehen aus Seeds per LLM: erst der Volltext, dann Titel, Beschreibung, Keywords
   und Frage-Antwort-Paare aus dem Volltext.

## Quellen

| Quelle | Verwendung | Lizenz |
|---|---|---|
| [OpenEduHub-Vokabulare](https://github.com/openeduhub/oeh-metadata-vocabs) (`educationalContext`, `new_lrt`, `intendedEndUserRole`, `hochschulfaechersystematik`) | Bildungsstufe, Inhaltstyp, Zielgruppe, Hochschulfächer | CC0 1.0 |
| Destatis-Systematik der Fächergruppen, Studienbereiche und Studienfächer | Ursprung der Hochschulfächersystematik | Statistisches Bundesamt; Quelle nennen |
| [OpenAlex Topics](https://github.com/ourresearch/openalex-topic-classification) (4.516 Themen mit Beschreibung und Keywords) | Themen Hochschule (Vertiefung/Forschung) | CC0 1.0 |
| [Gemeinsame Normdatei (GND)](https://lobid.org/gnd) über lobid-gnd | Beleg und Label der Grundlagenthemen Hochschule | CC0 1.0 |
| [KIM-Bildungsstufen](https://w3id.org/kim/educationalLevel/) | `exactMatch` der Niveaustufen | CC0 1.0 |

Die Vokabulare liegen aufbereitet unter `data/vocabs/` (Abruf mit `scripts/fetch_vocabs.py`, Datum in
der Datei).

## Mappings Hochschule

**Fächer.** Die Fächer folgen der Destatis-Systematik in der OEH-Fassung
(`http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/…`): Fächergruppe → Studienbereich →
Studienfach. Jedes Thema bekommt ein bis drei Studienfächer. Studienbereich und Fächergruppe werden daraus
abgeleitet.

**Themen aus OpenAlex** (`themen_openalex.jsonl`):
1. Handmapping der 252 OpenAlex-Teilgebiete auf einen primären und bis zu zwei weitere Studienbereiche
   (`openalex_teilgebiet_studienbereich.csv`, redaktionell, überprüfbar).
2. LLM-Feinmapping je Thema: deutsches Label, deutsche Kurzbeschreibung, deutsche Keywords, ein bis drei
   Studienfächer aus der vollständigen Fächerliste (der Studienbereich aus Schritt 1 ist nur ein Hinweis)
   und die Lehrebene `grundlagen`, `vertiefung` oder `forschung`. Ungültige Notationen werden verworfen
   und die Anfrage wird wiederholt.

**Grundlagenthemen** (`themen_grundlagen.jsonl`): OpenAlex beschreibt Forschungsfelder, Grundlagen des
Grundstudiums fehlen dort weitgehend. Deshalb schlägt ein LLM je Studienfach Grundlagenthemen vor
(Standard 12). Übernommen werden nur Vorschläge, die als GND-Sachschlagwort existieren, also mit gleichem
Namen oder Synonym. Das Label stammt dann aus der GND, Quelle ist die GND-URI. Vorschläge ohne Treffer
stehen in `grundlagen_ohne_gnd.csv`. Taucht dasselbe Schlagwort bei mehreren Fächern auf, wird es
zusammengeführt (höchstens drei Fächer).

**Inhaltstypen** (`inhaltstypen.json`): redaktionelle Auswahl textfähiger Typen aus OEH `new_lrt`. Je Typ
ist festgelegt, welche Textsorte erzeugt wird, welche Medienform der Volltext hat (Text, Folien, Video-
oder Audio-Transkript), für welche Lehrebenen er passt und optional für welche Fächergruppen
(z. B. Versuchsanleitung nur für Natur-, Ingenieur-, Agrar- und Gesundheitswissenschaften).

**Niveaus** (`niveaus.json`): Bachelor 1.–2. Studienjahr, Bachelor ab 3. Studienjahr, Master. Die
Zuordnung zu den DQR- und KIM-Stufen ist dort hinterlegt. Ausgeschlossen werden Forschungsthemen im
1.–2. Studienjahr und Grundlagenthemen im Master.

## Seeds

Ein Seed ist ein JSON-Objekt (eine Zeile in JSONL). Feldnamen folgen dem edu-sharing/OEH-Schema:

| Feld | Inhalt | Entstehung |
|---|---|---|
| `ccm:educationalcontext` (+ `_DISPLAYNAME`) | Bildungsstufe, z. B. Hochschule | fest codiert je Bereich |
| `ccm:oeh_taxonid_university` (+ `_DISPLAYNAME`) | 1–3 Studienfächer | aus dem Thema |
| `ccm:oeh_lrt` (+ `_DISPLAYNAME`) | Inhaltstyp | passend zur Lehrebene, rotierend |
| `ccm:educationalintendedenduserrole` (+ `_DISPLAYNAME`) | Zielgruppe | gewichtet (85 % Lernende, 15 % Lehrende) |
| `cclom:general_language` | Sprache | fest `de` |
| `synth:thema` | Thema mit URI, Labels, Beschreibung, Keywords, Lehrebene, Studienbereich, Fächergruppe | Mapping |
| `synth:niveau` | Niveaustufe | gleichverteilt (mit Ausschlüssen) |
| `synth:textsorte`, `synth:medienform` | Steuerung der Generierung | aus dem Inhaltstyp |
| `synth:didaktische_funktion` | z. B. Einführung, Übung und Anwendung | in 60 % der Seeds, zufällig |
| `synth:bearbeitungsdauer_minuten` | 15, 30, 45 oder 90 | in 50 % der Seeds, zufällig |
| `synth:provenance` | Herkunft je Feld: Quelle, URI, Lizenz, Methode | automatisch |
| `synth:seed_parameter` | Gleichverteilungsfeld, Gruppe, Zufalls-Seed | automatisch |

Kontrollierte Werte stehen wie in edu-sharing als Liste von URIs. Das Label steht in `<feld>_DISPLAYNAME`
und ist unverändert aus dem Vokabular übernommen. Für Prompts werden Labels bereinigt (ohne Präfix
„Studienbereich“, ohne Verwaltungszusätze in Klammern).

**Gleichverteilung.** Für jeden Bildungsbereich wählt man ein Feld (Hochschule: `thema` als Standard,
außerdem `studienfach`, `studienbereich`, `faechergruppe` und `lehrebene`). Die Themen werden nach diesem
Feld gruppiert, maßgeblich ist jeweils der erste Wert. Die gewünschte Anzahl wird gleichmäßig auf die
Gruppen verteilt, der Rest auf zufällig gewählte Gruppen. Innerhalb einer Gruppe kommen die Themen in
zufälliger Reihenfolge, jedes einmal, bevor sich eines wiederholt. Ab `n` ≥ Anzahl Themen ist bei
`thema` also jedes Thema dabei. Der Inhaltstyp wird je Gruppe rotiert: Gewählt wird jeweils der bisher
seltenste passende Typ.

**Reproduzierbarkeit.** Gleiche Mappings, gleiche Einstellungen und gleicher Zufalls-Seed ergeben
identische Seeds. Neben jeder Seed-Datei liegt eine `.stats.json` mit Parametern und Verteilungen.

## Materialien

Pro Seed zwei LLM-Aufrufe (OpenAI-kompatible API, Standardmodell `gpt-6-luna`):

1. **Volltext** aus dem Seed: Prompts `volltext_system.md` und `volltext.md`, etwa eine A4-Seite
   (400–550 Wörter). Kürzere Texte werden verworfen und neu angefragt.
2. **Metadaten nur aus dem Volltext** (plus Inhaltstyp): `metadaten_system.md` setzt die Vorgaben aus
   `titel.md`, `beschreibung.md`, `keywords.md` und `fragen_antworten.md` zusammen. Ergebnis:
   `cclom:title`, `cclom:general_description`, `cclom:general_keyword`, `synth:frage_antwort_paare`
   (Ziel: 10 Paare mit Lernzielstufe nach Bloom/Anderson, mindestens 8). Weil das LLM hier den Seed
   nicht sieht, lassen sich die Metadaten wirklich aus dem Text ableiten. Das ist wichtig, wenn mit
   den Daten ein Modell „Text → Metadaten“ trainiert werden soll.

Jedes Material behält alle Seed-Felder und bekommt `synth:volltext` sowie `synth:generierung` (Modell,
Base-URL, Datum, Hash der verwendeten Prompts).

**Parallelität, Wiederholung, Fortsetzung.** Standard sind 20 Worker. Fehlgeschlagene Aufrufe werden mit
wachsender Wartezeit wiederholt (Standard 6 Versuche). Ist die API länger als die eingestellte Zeit nicht
erreichbar (Standard 10 Minuten), pausiert der Lauf geordnet. Ergebnisse werden sofort zeilenweise
geschrieben. Ein erneuter Start mit derselben Ausgabedatei überspringt alles Fertige und versucht
Fehlgeschlagenes erneut (`<ausgabe>.fehler.jsonl`). Eine Lock-Datei verhindert zwei gleichzeitige Läufe
auf derselben Datei.

## Platzhalter

In den Prompt-Vorlagen (`prompts/`, in der Oberfläche editierbar) stehen Platzhalter als `{{name}}`.
Bedingte Abschnitte `{{#name}}…{{/name}}` erscheinen nur, wenn der Wert gesetzt ist.

| Platzhalter | Inhalt |
|---|---|
| `textsorte`, `inhaltstyp`, `medienform` | aus dem Inhaltstyp |
| `thema`, `thema_beschreibung`, `thema_keywords` | aus dem Thema (deutsch) |
| `fach`, `bildungsstufe`, `niveau`, `zielgruppe` | aus den Metadaten (bereinigte Labels) |
| `didaktische_funktion`, `bearbeitungsdauer` | optional |
| `prompt_titel`, `prompt_beschreibung`, `prompt_keywords`, `prompt_fragen_antworten` | nur in `metadaten_system.md` |

## Bekannte Grenzen

- OpenAlex-Themen stammen aus der Forschungsliteratur. Medizin, Biologie und Ingenieurwesen sind dort
  stark, Philologien, Kunst und Musik schwach vertreten. Die Grundlagenthemen und die Gleichverteilung
  nach Fach gleichen das teilweise aus.
- LLM-Zuordnungen sind nicht deterministisch. In Stichproben schwankte die Lehrebene bei etwa 10 % der
  Themen zwischen zwei Läufen, die Fächer überlappten meist. Die Zuordnungen sind deshalb als Vorschlag
  zu verstehen und in den JSONL-Dateien prüfbar.
- Synthetische Texte können fachliche Fehler enthalten. Für Trainingszwecke ist die Abbildung Text →
  Metadaten wichtiger als die fachliche Tiefe. Für eine Veröffentlichung als Lernmaterial sind sie nicht
  geprüft.

## Neuen Bildungsbereich anlegen

1. `sem/bereiche/<bereich>/__init__.py` mit einer Klasse, die `Bildungsbereich` implementiert
   (`topics`, `balance_key`, `make_seed`, `prompt_values`), und in `sem/bereiche/__init__.py`
   registrieren.
2. Mappings unter `data/mappings/<bereich>/` ablegen, gleiche Themenstruktur wie bei Hochschule
   (`id`, `label_de`, Fachfelder mit `uri`/`label`, `quelle`, `mapping`).
3. Optional eigene Prompts unter `prompts/<bereich>/`.
