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

**Themen aus OpenAlex** (`themen_openalex.jsonl`). Jeder Schritt ist einzeln aufrufbar und fortsetzbar
(`python -m sem mappings hochschule --liste`):

1. `fachprofile`: Das LLM beschreibt jedes der 273 Studienfächer in zwei Sätzen, nennt typische Themen
   und grenzt es von Nachbarfächern ab (`studienfach_profile.jsonl`, auch für sich nutzbar).
2. `uebersetzen`: deutsches Label, Kurzbeschreibung und Keywords je Thema. Vorhandene Übersetzungen
   werden übernommen.
3. `kandidaten`: Ein lokales, mehrsprachiges Embedding-Modell (Standard `intfloat/multilingual-e5-small`,
   CPU) vergleicht jedes Thema mit allen Fachprofilen. Kandidaten sind die 15 ähnlichsten Fächer plus je
   Studienbereich aus dem Handmapping (`openalex_teilgebiet_studienbereich.csv`) dessen zwei ähnlichste
   Fächer. So wählt das LLM aus einer kurzen, plausiblen Liste statt aus 273 Fächern.
4. `faecher`: drei unabhängige Stimmen je Thema. Jede Stimme nutzt eine andere Perspektive (Fachsystematik,
   Studienberatung, Bibliothek), eine andere Reihenfolge der Kandidaten und andere Nachbarthemen im
   Batch. Je gewähltem Fach nennt das LLM eine Begründung und Belegbegriffe aus dem Thementext.
5. `lehrebene`: ebenfalls drei Stimmen, nach festen Kriterien (Einführungsmodul, eigenes Teilgebiet,
   Spezialforschung).
6. `zusammenfuehren`: Ein Fach wird übernommen, wenn es mindestens zwei von drei Stimmen hat (höchstens
   drei Fächer). Je Thema stehen unter `mapping`:
   - `status`: `gesichert` (Hauptfach in allen Stimmen, meist an erster Stelle), `wahrscheinlich`
     (Mehrheit), `unsicher` (keine Mehrheit) oder `redaktionell` (korrigiert),
   - `konfidenz`: Stimmenanteil des Hauptfachs,
   - je Fach: Stimmen, Embedding-Ähnlichkeit, Begründungen und `belegbegriffe_im_text`. Das sind nur die
     Belegbegriffe, die wirklich im Thementext vorkommen, sie werden automatisch geprüft,
   - `verworfen`: Fächer mit zu wenigen Stimmen, `handmapping_bestaetigt` und die Stimmen zur Lehrebene.

   Unsichere Fälle, Abweichungen vom Handmapping und Hauptfächer ohne Beleg im Text stehen in
   `pruefliste_themen.csv`. Wer dort Korrekturen einträgt (`korrektur_codes` als Notationen mit `|`,
   `korrektur_lehrebene`) und die Datei als `korrekturen.csv` speichert, überschreibt beim nächsten
   Zusammenführen das LLM-Ergebnis.

Seeds übernehmen `status` und `konfidenz` in die Herkunftsangabe des Fachs.

Stand der mitgelieferten Datei (gpt-6-luna, multilingual-e5-small, drei Stimmen, 4.516 Themen):
Fach 4.205 gesichert, 297 wahrscheinlich, 14 unsicher; Lehrebene 3.436 gesichert, 1.079 wahrscheinlich,
1 unsicher; Handmapping bei 3.629 Themen bestätigt; 101 Themen auf der Prüfliste. Sechs Themen erhielten
in keiner Stimme ein Fach; sie tragen das ähnlichste Fach laut Embedding (`mapping.ersatz`) und stehen
auf der Prüfliste.

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
- LLM-Zuordnungen sind nicht deterministisch. Die Mehrfachabstimmung macht die Schwankung sichtbar
  (Status und Konfidenz), beseitigt sie aber nicht. Drei Stimmen desselben Modells können sich
  gemeinsam irren; Embedding-Ähnlichkeit und Handmapping dienen als unabhängige Gegenprobe.
- Die Lehrebene ist ungleich verteilt (94 grundlagen, 2.810 vertiefung, 1.612 forschung). Weil Forschungs-
  themen im 1.–2. Studienjahr ausgeschlossen sind, ist dieses Niveau bei Gleichverteilung nach Thema
  seltener (bei 30.000 Seeds rund 22 % statt 33 %). Die Grundlagenthemen gleichen das aus.
- Synthetische Texte können fachliche Fehler enthalten. Für Trainingszwecke ist die Abbildung Text →
  Metadaten wichtiger als die fachliche Tiefe. Für eine Veröffentlichung als Lernmaterial sind sie nicht
  geprüft.

## Neuen Bildungsbereich anlegen

1. `sem/bereiche/<bereich>/__init__.py` mit einer Klasse, die `Bildungsbereich` implementiert
   (`topics`, `balance_key`, `make_seed`, `prompt_values`), und in `sem/bereiche/__init__.py`
   registrieren.
2. Mappings unter `data/mappings/<bereich>/` ablegen, gleiche Themenstruktur wie bei Hochschule
   (`id`, `label_de`, Fachfelder mit `uri`/`label`, `quelle`, `mapping`).
3. Zum Bauen der Mappings `mapping_schritte()` und `mapping_folge` setzen. CLI und Oberfläche zeigen die
   Schritte dann automatisch. Die Bausteine in `sem/mapping/` sind bereichsunabhängig: Embedding-
   Kandidaten (`embeddings.py`), Mehrfachabstimmung mit Belegen (`abstimmung.py`), Prüfliste und
   Korrekturen (`pruefung.py`). Ein Schulbereich würde etwa Themen auf Schulfächer, ein
   Berufsbildungsbereich ESCO-Kompetenzen auf Berufe abstimmen.
4. Optional eigene Prompts unter `prompts/<bereich>/`.
