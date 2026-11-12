# Prompt-Feedback zur Datensatzgenerierung

Stand: 6. November 2026

Datenbasis: [`JanSchachtschabel/synthetic-german-educational-materials-50k`](https://huggingface.co/datasets/JanSchachtschabel/synthetic-german-educational-materials-50k),
Revision `38817be749110314ab7be6508c5b4055e291bc73`, Trainingssplit mit 45.144 Materialien
(Validation 2.463, Test 2.393). Alle Zahlen unten beziehen sich auf den Trainingssplit.

Diese Datei sammelt Beobachtungen und Verbesserungsvorschläge zu **allen Prompts, die in die
Datensatzgenerierung eingehen**. Sie ist Feedback, keine aktive Vorlage: Die Dateien unter
`prompts/` bleiben unverändert. Änderungen an Prompts wirken sich erst auf neu erzeugte Daten aus;
Titel-, Beschreibungs- und Keyword-Änderungen erfordern anschließend ein neues Training.

> **Umsetzung (12. November 2026):** Die Vorschläge sind weitgehend in die aktiven Prompts und Prüfungen
> übernommen: Gender-Doppelpunkt im Titel erlaubt, Bloom-Typ wird geprüft und vereinheitlicht, Layout-Fragen
> ausgeschlossen, Inhaltstyp-Kategorien mit Textsorten-Beschreibung, Beschreibung ohne Schablonen-Anfang
> (bei Bedarf neu angefragt), mindestens 5 Keywords, Umfang je Inhaltstyp, Einstieg und Namen aus dem Seed,
> keine Kästchen- und Rahmengrafiken im Volltext. Der Abschnitt unten beschreibt den Stand vor der Änderung.

## Kurzfassung

| Priorität | Prompt | Wichtigster Befund |
|---|---|---|
| **P2** | `titel.md` | Die neue Fassung (Commits #16/#17) passt zum Datensatz. Randfälle: 5,3 % über 60 Zeichen, 0,99 % Kommas, 0,07 % Klammern, 0,04 % Anführungszeichen; der Parser lehnt Gender-Doppelpunkte (`Patient:innen`) fälschlich ab. |
| **P1** | `fragen_antworten.md` | 98,8 % genau 10 Paare, aber 12 Paare mit leerem oder fremdsprachigem `typ`; 0,97 % der Fragen beziehen sich auf Layout/Formatierung. Der Parser prüft den Typ nicht. |
| **P1** | `inhaltstyp.md` | Nur 79,7 % Übereinstimmung zwischen Seed-Materialart und Modellentscheidung. Die Kategorien werden ohne Definition nur als Name → ID gezeigt. |
| **P1** | `berufsbildung/volltext.md` | Latenter Textfehler: `{{#kompetenzart}}die Kompetenz{{/kompetenzart}}{{#lernfeld}}das Lernfeld{{/lernfeld}}` ergibt bei gleichzeitig gesetzten Feldern „die Kompetenzdas Lernfeld“. |
| **P2** | `beschreibung.md` | 41,3 % der Beschreibungen sind länger als die verlangten 800 Zeichen; 14,5 % haben einen Satz mit mehr als 20 Wörtern. Der Parser erlaubt bis 1.200 Zeichen. |
| **P2** | `system.md` / `material.md` | Der Systemprompt sagt, Titel, Beschreibung und Keywords stützen sich gegenseitig, der Titel wird aber zuerst erzeugt und sieht die anderen Felder nie. |
| **P2** | `volltext_system.md` | Es gibt keine Vorgabe für Zwischenüberschriften, obwohl der Modus `condensed` und die spätere Segmentierung von Abschnittsstruktur abhängen. |
| **P3** | `keywords.md` | Inhaltlich sehr gut (100 % 5–8 Keywords, keine Duplikate, keine Materialtypen). Aber 57,6 % haben genau 8 Keywords: Das Modell füllt systematisch das Maximum. |
| **P3** | `ausgabesprache.md` | Die Regel „feste Kennungen bleiben unverändert“ reicht nicht: Im `typ`-Feld der Q&A stehen vereinzelt `appliquer`, `analizar`, `применять` usw. |
| **P3** | `volltext.md` / Bereichsprompts | 84,5 % der Volltexte liegen im Zielbereich 400–550 Wörter; die Bereichs-Prompts sind stimmig, aber sprach Kurs-/Fremdsprachenfälle kollidieren mit `ausgabesprache`. |

## Titel: `prompts/titel.md`

### Stand

Der Titel-Prompt wurde mit den Commits #16 und #17 neu gefasst: 3–8 Wörter, höchstens
60 Zeichen, natürliche zusammenhängende Wortgruppe statt Schlagwortkette, höchstens zwei
verbundene Fachbegriffe, keine Lernziel-Verben, kein Doppelpunkt oder Gedankenstrich. Der
Zeitstempel passt dazu: Der Datensatz wurde am 5.11. um 22:43 UTC hochgeladen, also nach #16 und
vor #17. Die Verteilung bestätigt den Wechsel:

| Kennzahl | Alter Datensatz | Aktueller Datensatz |
|---|---:|---:|
| Median Länge | 67 Zeichen | **45 Zeichen** |
| Kürzer als 40 Zeichen | 0,0 % | **26,1 %** |
| Länger als 60 Zeichen | – | **5,3 %** |
| Länger als 70 Zeichen | 33,7 % | 0,6 % |
| 3–8 Wörter | 91,9 % | **99,4 %** |
| Enthält Artikelform (der/die/das …) | 6,0 % | **28,4 %** |
| Wiederholtes Wort | 6,7 % | **2,1 %** |
| Satzzeichen | 0,3 % | 1,3 % |
| Materialtyp im Titel | 0,2 % | 0,3 % |
| Zielgruppenangabe im Titel | 0,9 % | 0,4 % |

Beispiele aus dem aktuellen Datensatz:

- „Was eine Kerzenflamme zum Brennen braucht“ (41 Zeichen)
- „Viskosität von Beschichtungen prüfen“ (36 Zeichen)
- „Das Betriebssystem im Alltag bedienen“ (37 Zeichen)
- „Regelung und Zustandsüberwachung in der Luftfahrt“ (49 Zeichen)

Die neuen Titel sind kürzer, natürlicher, deutlich seltener repetitiv und enthalten mehr Artikel
und Verbformen. Das ist eine bewusste Stiländerung. Der Repository-Prompt beschreibt jetzt
denselben Stil; die frühere Keyword-Ketten-Vorgabe ist ersetzt.

### Restpunkte (P2/P3)

1. **Länge:** Der Prompt verlangt höchstens 60 Zeichen, `parse_titel` erlaubt 80. 5,3 % der Titel
   liegen über 60, 0,6 % über 70. Die Toleranz ist bewusst (Kommentar im Code), sollte aber
   dokumentiert bleiben. Wer die 60er-Grenze durchsetzen will, sollte den Parser auf 70 senken
   und die Titel neu erzeugen.
2. **Kommas:** 0,99 % der Titel enthalten ein Komma, obwohl der Prompt „keine Kommaliste“ sagt.
   Die Beispiele sind meist natürliche Aufzählungen („Kultur, Institutionen und wirtschaftliche
   Entwicklung“). Besser präzisieren: „Keine Schlagwortliste mit Komma; ein Komma in einer
   natürlichen Aufzählung ist erlaubt.“
3. **Klammern und Anführungszeichen:** 0,07 % enthalten Klammern (33 Titel, z. B. „(MRT)“,
   „(E)-But-2-en“), 0,04 % Anführungszeichen (16 Titel, z. B. „Der Hölle Rache“). Der Prompt
   verbietet beides pauschal. Für Fachabkürzungen und Werk-/Personentitel sollte es eine eng
   begrenzte Ausnahme geben, sonst gehen präzise Titel verloren.
4. **Gender-Doppelpunkt:** `parse_titel` lehnt jeden Doppelpunkt ab. Dadurch werden
   „Patient:innenperspektive“ und „Kindersoldat:innen“ fälschlich zurückgewiesen. Die Regel
   sollte nur Untertitel-Doppelpunkte mit Leerzeichen (`": "`) und Gedankenstriche mit
   Leerzeichen treffen, nicht den Gender-Doppelpunkt mitten im Wort.
5. **Alte Reste:** 105 Titel (0,23 %) sind mit `title_previous` identisch; 17 Titel (0,04 %)
   würden vom aktuellen Parser abgelehnt. Der Datensatz wurde vor #17 exportiert; damals
   behielten 145 Materialien wegen der Zeichenprüfung ihren alten Titel. Für den nächsten Export
   `python -m sem titel-neu` erneut laufen lassen und neu exportieren. Für das anstehende
   Training sind 0,2 % Altlasten vernachlässigbar.

Der frühere Vorschlag, den Prompt komplett neu zu schreiben, ist damit erledigt: Die neue
Fassung ist bereits im Repository und im Datensatz wirksam.

### Auswirkung auf das Training

Das anstehende Retraining nutzt die neuen Titel als Ziel. Das ist richtig so. Der alte Checkpoint
sollte **nicht** fortgesetzt werden, weil er drei Epochen lang den alten Keyword-Stil gesehen hat.
Die Modellkarte sollte den neuen Stil beschreiben (Median 45 Zeichen, 5 Wörter, 28,4 % mit
Artikelformen), nicht mehr die alten Keyword-Ketten.

## Beschreibung: `prompts/beschreibung.md`

### Beobachtung

- Median 782 Zeichen, p95 922, Maximum 1.129.
- Nur 58,7 % liegen im verlangten Bereich 400–800 Zeichen.
- 41,3 % sind länger als 800 Zeichen, obwohl der Prompt das ausschließt.
- Der Parser akzeptiert 250–1.200 Zeichen, also praktisch alle erzeugten Beschreibungen.
- 14,5 % enthalten mindestens einen Satz mit mehr als 20 Wörtern (Prompt: maximal 20).
- Nur 5 von 45.144 Beschreibungen enthalten eine verbotene Floskel oder URL (0,0 %).
- Häufigste Satzanfänge: „Im …“ (37,7 %), „Das …“ (34,5 %), „Die …“ (14,1 %), „Der …“ (6,8 %).

Die inhaltliche Qualität ist gut. Das Problem ist die **Längen- und Satzregel**: Sie wird
formuliert, aber nicht zuverlässig eingehalten, und der Validator ist zu großzügig, um sie
durchzusetzen.

### Vorschlag (P2)

1. Länge über Sätze statt Zeichen steuern:
   - „Länge: 4–6 Sätze, insgesamt etwa 500–900 Zeichen (höchstens 1.000).“
   - Das passt besser zur tatsächlichen Verteilung (Median 782, p95 922) als die strikte
     400–800-Grenze.
2. Satzlänge als prüfbare Regel formulieren:
   - „Jeder Satz hat höchstens 20 Wörter; teile lange Sätze auf.“
3. Gegen die monotone Eröffnung:
   - „Beginne nicht schematisch mit ‚Im‘ oder ‚Das‘; variiere den Einstieg je nach Inhalt.“
4. Parser anpassen: Wenn 500–900 gewünscht sind, sollte `parse_beschreibung` z. B. 400–1.000
   akzeptieren und bei Überschreitung neu anfragen. Wenn die großzügige Prüfung bewusst gewählt
   wurde, um Retries zu sparen, sollte das im Prompt-README dokumentiert werden.

## Keywords: `prompts/keywords.md`

### Beobachtung

- 100 % der Materialien haben 5–8 Keywords.
- Keine case-insensitiven Duplikate.
- 0 % Materialtyp- oder Zielgruppenwörter in den Keywords.
- Verteilung: 8 Keywords bei 57,6 %, 7 bei 40,2 %, 6 bei 2,1 %, 5 bei 0,1 %.
- Der Parser akzeptiert mindestens 4 Keywords, der Prompt verlangt 5–8.

### Bewertung

Der Prompt funktioniert sehr gut. Die einzige Auffälligkeit ist die starke Konzentration auf
genau 8 Keywords: Das Modell füllt offenbar das Maximum. Das ist kein Fehler, macht die
Keyword-Listen aber gleichförmig.

### Vorschlag (P3)

- Parser-Minimum auf 5 anheben, damit die Prompt-Regel und die Prüfung übereinstimmen.
- Optional im Prompt: „5 bis 8 Keywords; wähle nur so viele, wie für das Thema wirklich nötig
  sind“ oder je Seed eine Zielzahl zwischen 5 und 8 vorgeben.
- Die Regel „ausschließlich Substantive im Nominativ Singular“ um feste Fachbegriffe,
  Abkürzungen und übliche Zitierformen ergänzen, z. B. „Lineare Regression“, „DSGVO“,
  „mRNA-Impfstoff“.

## Inhaltstyp: `prompts/inhaltstyp.md`

### Beobachtung

- Die vom Modell aus dem fertigen Material bestimmte Materialart stimmt in **79,7 %** der Fälle
  mit dem Seed-Wert überein; in 20,3 % nicht.
- Der Prompt zeigt die Kategorien nur als `Name → id`, ohne Definition oder Beispiel.
- Der Prompt verlangt ausschließlich die ID; der Parser akzeptiert auch den Namen.

### Bewertung

20 % Abweichung ist für eine reine Prüfentscheidung viel. Ursachen können sein:

- überlappende Kategorien (z. B. „Übung“ vs. „Arbeitsblatt“),
- fehlende Definitionen, was die dominante didaktische Funktion ausmacht,
- Mischformen, bei denen der Seed-Wert selbst unscharf ist.

Die Abweichung ist nicht automatisch ein Modellfehler: Der Seed-Wert kann falsch oder die
Kategorie mehrdeutig sein. Für die Datenqualität ist sie aber ein Signal.

### Vorschlag (P1)

- In `{{kategorien}}` zusätzlich eine kurze Definition je Kategorie ausgeben, z. B.
  `- Arbeitsblatt → arbeitsblatt: Aufgaben zum Bearbeiten/Schreiben, oft mit Lösungen`.
- Zwei bis drei Grenzfälle als Beispiele in den Prompt aufnehmen
  („Ein Foliensatz mit Aufgaben ist eher Präsentation als Arbeitsblatt“).
- Im Prompt klarstellen: „Bei Mischformen zählt die Funktion, die Lernende tatsächlich ausführen.“
- Prüfen, ob die 20,3 % Abweichungen systematisch bei bestimmten Kategorien auftreten; dann
  zuerst die Kategoriendefinitionen schärfen, nicht den Prompt weiter verschärfen.

## Frage-Antwort-Paare: `prompts/fragen_antworten.md`

### Beobachtung

- 98,8 % der Materialien haben genau 10 Paare; 1,2 % haben 9, 11 oder 12.
- 100 % haben mindestens 8 Paare und mindestens 3 ab „anwenden“.
- 0,97 % der Fragen beziehen sich auf Layout, Formatierung, Seiten oder Abbildungen, obwohl der
  Prompt das ausdrücklich ausschließt.
- 0,2 % der Antworten haben mehr als 4 Sätze.
- 0,03 % der Fragen sehen wie Ja/Nein-Fragen aus.
- 12 Paare haben einen leeren oder fremdsprachigen `typ`: `appliquer`, `analizar`, `analyser`,
  `aplicar`, `анализировать`, `toepassen`, `evaluar`, `crear` usw. Der Parser prüft den Typ nicht.

### Bewertung

Der Prompt ist gut: exakt 10 Paare, Bloom-Stufen und mindestens drei anwendungsorientierte Paare
werden fast immer eingehalten. Die Ausreißer sind selten, aber sie zeigen zwei Lücken:

1. Das `typ`-Feld ist nicht gegen die sechs erlaubten Werte abgesichert.
2. „Keine Fragen zu Aufbau/Formatierung“ wird gelegentlich ignoriert.

### Vorschlag (P1)

- `parse_paare` soll `typ` gegen
  `{"erinnern", "verstehen", "anwenden", "analysieren", "bewerten", "erschaffen"}` prüfen.
  Unbekannte Werte: normalisieren (z. B. `appliquer` → `anwenden`) oder das Paar neu anfragen.
- Im Prompt ergänzen:
  - „Das Feld `typ` enthält immer einen der sechs deutschen Werte – auch dann, wenn die übrige
    Ausgabe in einer anderen Sprache ist.“
  - „Keine Fragen nach Seitenzahlen, Kapiteln, Abbildungen, Tabellen, Layout oder Formatierung;
    frage nach dem fachlichen Inhalt.“
- Zwei Negativbeispiele aufnehmen, z. B. „Welche Abbildung zeigt …?“ (nicht erlaubt) und
  „Warum führt eine höhere Temperatur zu …?“ (erlaubt).
- Optional die Antwortlänge im Parser prüfen (maximal 4 Sätze).

## Volltext: `prompts/volltext.md` und `prompts/volltext_system.md`

### Beobachtung

- Median 477 Wörter, p5 400, p95 584, Maximum 867.
- 84,5 % liegen im Zielbereich 400–550 Wörter.
- 10,6 % sind länger als 550 Wörter.
- 0 % sind kürzer als 150 Wörter; der Parser akzeptiert aber bereits ab 150 Wörtern.

### Bewertung

Der Volltext-Prompt funktioniert gut. Die Längenabweichungen nach oben sind moderat und für
bestimmte Textsorten (Skript, Fallstudie, Transkript) plausibel. Die Prüfung ab 150 Wörtern ist
sehr großzügig: Ein 200-Wörter-Text würde als gültig gelten, obwohl der Prompt 400–550 verlangt.

### Vorschlag (P2)

- Parser-Minimum auf 300 Wörter anheben (oder zumindest eine Warnung protokollieren).
- In `volltext_system.md` eine Gliederungsregel ergänzen:
  - „Gliedere längere Texte mit 3–6 aussagekräftigen Zwischenüberschriften (`##`), außer bei
    Textsorten ohne Gliederung (Transkript, Dialog, Foliensatz).“
  - Das hilft dem Modus `condensed`, der Überschriften und erste Sätze je Abschnitt nutzt.
- Die Regel „keine Titelzeile“ ist gut und sollte bleiben; der Titel wird separat erzeugt.

## Gemeinsamer System- und Materialprompt: `system.md`, `material.md`

### Beobachtung

`system.md` sagt:

> Vorhandene Felder sind Kontext und stützen sich gegenseitig
> (Titel + Beschreibung + Schlagworte ergeben mehr als jedes Feld allein).

Tatsächlich ist die Reihenfolge in `sem/generate.py`:

1. Volltext
2. **Titel**
3. Beschreibung
4. Keywords
5. Inhaltstyp und Fragen

Der Titel sieht Beschreibung und Keywords also nie. Die Aussage ist für den Titel irreführend.

Außerdem steht in `system.md`:

> Gib ausschließlich den geforderten Wert zurück – ohne Einleitung, Anführungszeichen, Markdown
> oder Codeblöcke.

Das kollidiert mit dem JSON-Auftrag der Frage-Antwort-Paare, der Anführungszeichen und
geschweifte Klammern verlangt. Die Q&A funktionieren trotzdem, aber die Regel ist widersprüchlich.

### Vorschlag (P2)

- Entweder die Reihenfolge ändern:
  `Volltext → Beschreibung → Keywords → Titel → Inhaltstyp/Fragen`
  und den Titel die bereits erzeugten Felder sehen lassen. Das kann die Konsistenz erhöhen, muss
  aber auf 100–200 Materialien getestet werden, weil der Titel dann Keywords oder
  Beschreibungsformulierungen übernehmen kann.
- Oder den Systemprompt ehrlich formulieren:
  „Der Titel wird zuerst erzeugt und stützt sich nur auf den Volltext und die Seed-Metadaten;
  Beschreibung und Keywords stützen sich zusätzlich auf den Titel.“
- Ausgabe-Regel ergänzen:
  „Ausnahme: Bei JSON-Aufträgen (Frage-Antwort-Paare) gilt exakt das dort gezeigte JSON-Schema;
  Anführungszeichen sind dort erforderlich.“

## Bereichsspezifische Volltext-Prompts

### `prompts/berufsbildung/volltext.md` (P1)

Latenter Fehler in der Auftragszeile:

```text
Beziehe {{#kompetenzart}}die Kompetenz{{/kompetenzart}}{{#lernfeld}}das Lernfeld{{/lernfeld}} auf ...
```

Sind beide Felder gesetzt, entsteht „Beziehe die Kompetenzdas Lernfeld“. Aktuell schließen sich
die Felder im Seed aus, aber der Prompt sollte nicht von dieser Annahme abhängen.

Vorschlag:

```text
Beziehe {{#kompetenzart}}die Kompetenz „{{thema}}“{{/kompetenzart}}{{#lernfeld}}{{#kompetenzart}} bzw. {{/kompetenzart}}das Lernfeld „{{lernfeld}}“ (KMK-Rahmenlehrplan {{rahmenlehrplan}}){{/lernfeld}} auf ...
```

Zusätzlich: `{{rahmenlehrplan}}` nur ausgeben, wenn es gesetzt ist, sonst bleibt „()“ stehen.

### `prompts/schule/volltext.md` (P2)

- Die Zeile mit den vielen bedingten Abschnitten ist korrekt, aber schwer zu prüfen. Ein
  `{{#anforderungsbereich}} / {{anforderungsbereich}}{{/anforderungsbereich}}` ist nur sinnvoll,
  wenn der Wert das Label schon enthält; das sollte im Seed sichergestellt sein.
- `{{#zielsprache}}` (Fremdsprachenunterricht) und `ausgabesprache` können gleichzeitig aktiv
  sein. Dann konkurrieren „Texte und Aufgaben überwiegend in der Zielsprache“ und „alles in der
  Ausgabesprache“. Vorrangregel ergänzen: Bei Sprachkursen gilt die Zielsprache für Texte und
  Aufgaben, die Ausgabesprache für Erklärungen und Metadaten.

### `prompts/erwachsenenbildung/volltext.md` (P2)

- `{{#zielsprache}}` sagt „Erklärungen bei Bedarf auf Deutsch“, während `ausgabesprache.md`
  „alle Ausgaben in der Ausgabesprache“ verlangt. Für einen Sprachkurs mit Ausgabesprache
  Englisch ist das widersprüchlich. Vorrangregel wie oben ergänzen.
- `{{#einfache_sprache}}` und `{{#zielsprache}}` können kombiniert werden; der Prompt sollte
  sagen, dass einfache Sprache auch für die Zielsprache gilt.

### `prompts/elementar/volltext.md` (P3)

- Der Umfang 350–500 Wörter weicht bewusst von den 400–550 der anderen Bereiche ab. Das ist
  plausibel, sollte aber im Prompt-README als Ausnahme dokumentiert sein.
- Die Sicherheits- und Vielfaltsregeln sind gut. Keine Änderung nötig.

## Ausgabesprache: `prompts/ausgabesprache.md`

### Beobachtung

Der Prompt wird nur bei einer Ausgabesprache ungleich Deutsch angehängt. Der aktuelle Datensatz
ist vollständig deutsch, daher ist die Wirkung dort nicht messbar. Die 12 fremdsprachigen
`typ`-Werte in den Q&A zeigen aber, dass die Regel „feste Kennungen bleiben unverändert“ zu
schwach ist.

### Vorschlag (P3)

- Explizit ergänzen: „Das Feld `typ` der Frage-Antwort-Paare enthält immer einen der sechs
  deutschen Werte `erinnern`, `verstehen`, `anwenden`, `analysieren`, `bewerten`, `erschaffen` –
  unabhängig von der Ausgabesprache.“
- Für Keywords in anderen Sprachen die deutsche Grammatikregel relativieren:
  „Verwende Substantive in der üblichen Zitierform der Zielsprache.“
- Prüfen, ob die Reihenfolge „Systemprompt + Ausgabesprache-Hinweis“ bei JSON-Aufträgen
  ausreicht; ggf. den Hinweis auch im User-Prompt wiederholen.

## Prompt gegen Prüfung: systematische Abweichungen

Die Prüffunktionen in `sem/generate.py` sind bewusst großzügiger als die Prompts, vermutlich um
Retries und Kosten zu sparen. Dadurch wird aber nicht sichtbar, wie oft ein Prompt verletzt wird.

| Feld | Prompt-Vorgabe | Prüfung | Beobachtung im Datensatz |
|---|---|---|---|
| Titel | 3–8 Wörter, höchstens 60 Zeichen | 10–80 Zeichen | 99,4 % 3–8 Wörter; 5,3 % über 60 Zeichen; 0,04 % vom Parser abgelehnt |
| Beschreibung | 400–800 Zeichen | 250–1.200 Zeichen | 41,3 % über 800 Zeichen |
| Keywords | 5–8 | mindestens 4, maximal 8 | 100 % 5–8; 57,6 % genau 8 |
| Volltext | 400–550 Wörter (Elementar 350–500) | mindestens 150 Wörter | 84,5 % im Zielbereich; 0 % unter 150 |
| Q&A | genau 10, ≥3 ab „anwenden“, sechs Bloom-Typen | mindestens 8, keine Typprüfung | 98,8 % genau 10; 12 fehlerhafte `typ`-Werte |
| Inhaltstyp | nur ID | ID oder Name | 79,7 % Übereinstimmung mit dem Seed |

Empfehlung: Entweder die Prompts an die Prüfung anpassen (großzügige Vorgaben) oder die Prüfung
an die Prompts (strengere Vorgaben, mehr Retries). Beides zu lassen führt dazu, dass die
Datensatzbeschreibung und die tatsächliche Verteilung auseinanderfallen.

## Empfohlene Reihenfolge für die nächste Datengenerierung

1. **Titel-Prompt** nur noch in Randfällen schärfen (Klammern für Abkürzungen, Anführungszeichen
   für Werk-/Personentitel, Komma-Regel) und den Gender-Doppelpunkt im Parser erlauben.
2. **Q&A-Typprüfung** in `parse_paare` ergänzen; fremdsprachige Typen normalisieren.
3. **Berufsbildung-Prompt** reparieren (fehlendes „bzw.“ / Leerzeichen).
4. **Inhaltstyp-Kategorien** mit Definitionen und Grenzfällen versehen.
5. **Beschreibung** auf Satzanzahl und maximale Satzlänge umstellen; Parser-Grenzen angleichen.
6. **Systemprompt** um die JSON-Ausnahme ergänzen; Reihenfolge Titel/Beschreibung/Keywords
   testweise ändern.
7. **Volltext-Systemprompt** um die Gliederungsregel ergänzen.
8. **Keywords** nur feinjustieren (Minimum 5, Zielzahl variieren).

## Bezug zum anstehenden Retraining

Das Retraining mit 4 Epochen nutzt den aktuellen Datensatz mit den neuen Titeln. Die
Prompt-Verbesserungen oben sind **nicht** Teil dieses Laufs; sie betreffen die nächste
Datengenerierung. Der Titel-Prompt ist bereits aktualisiert (#16/#17) und beschreibt denselben
natürlichen Stil wie der Datensatz. Ein erneutes Training ist nur nötig, wenn man die 0,2 %
alten Resttitel bereinigt oder die Randfälle aus dem Titel-Abschnitt ändert.

Für die Modellkarte bedeutet das: Die Titel im aktuellen Datensatz sind natürlicher und kürzer
(Median 45 Zeichen, 5 Wörter, 28,4 % mit Artikelformen). Die alte Aussage „Keyword-Ketten“
beschreibt nur noch die vorherige Datensatzversion.
