# Methodik

Kurzbeschreibung, wie Seeds und synthetische Materialien entstehen, damit Ergebnisse nachvollziehbar
und reproduzierbar sind. Jeder Datensatz trägt zusätzlich unter `synth:provenance` die Herkunft
jedes Feldes.

## Überblick

```
offene Vokabulare und Taxonomien ──► Mappings (Themen mit Fachzuordnung) ──► Seeds ──► Materialien
     (OEH, Destatis, OpenAlex, GND,      data/mappings/<bereich>/            ohne LLM     mit LLM
      hbz-Katalog, WLO-Keywords)
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
| [Gemeinsame Normdatei (GND)](https://lobid.org/gnd) über lobid-gnd, [GND-Sachgruppen](https://d-nb.info/standards/vocab/gnd/gnd-sc.html) | GND-Themen Hochschule (Label, Synonyme, Oberbegriffe, Sachgruppe) | CC0 1.0 |
| [hbz-Verbundkatalog](https://lobid.org/resources) über lobid-resources | Kandidaten und Belege: Häufigkeit eines Schlagworts in Lehrbüchern und Hochschulschriften | CC0 1.0 |
| [WirLernenOnline](https://wirlernenonline.de) (Export der Redaktion) | nur Keywords in Verbindung mit Hochschulfächern, als Kandidaten und Belege; keine Titel, Beschreibungen oder Inhalte | Keywords nur als Häufigkeiten bzw. Abgleich auf GND/OpenAlex |
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

**Themen aus GND und Katalog** (`themen_gnd.jsonl`, Folge `gnd`). OpenAlex beschreibt Forschungsfelder;
Grundlagen und klassische Lehrthemen fehlen dort weitgehend. Deshalb kommen weitere Themen aus der GND:

1. `bruecke`: Jedes Studienfach bekommt per Embedding-Kandidaten und drei Stimmen 1–3 GND-Sachgruppen
   (`studienfach_gnd_sachgruppe.jsonl`). Sachgruppen für Personen (`…p`) und Produkte (`…m`) sind
   ausgenommen.
2. `gnd_pool`: Kandidaten sind GND-Schlagwörter, die im hbz-Katalog häufig an **Lehrbüchern**
   (`natureOfContent` Lehrbuch, mindestens 3) oder **Hochschulschriften** (`type:Thesis`, mindestens 10)
   hängen, abgefragt je Notations-Präfix 000–999, sowie WLO-Keywords (siehe unten), die **exakt** dem
   Vorzugs- oder Synonymnamen eines GND-Schlagworts entsprechen. Jeder Begriff braucht einen
   Katalogbeleg (mindestens ein Lehrbuch oder drei Hochschulschriften), WLO allein reicht nicht.
   Zugelassen sind nur die GND-Typen Sachschlagwort und historisches Ereignis; Nomenklaturen (einzelne
   Stoffe, Arten) und Motiv-Schlagwörter (`<Motiv>`) werden ausgeschlossen. Personen, Körperschaften,
   Firmen, Orte, Werke, Produkte und Software haben in der GND eigene Typen und fallen dadurch heraus.
   Was nicht exakt passt, wird verworfen.
3. `gnd_relevanz`: drei Stimmen, ob der Begriff Lehrinhalt an Hochschulen ist (auch wenn er außerdem in
   der Schule vorkommt, z. B. Chemie) oder nur in der Schule, in der Berufsbildung, im Alltag oder gar nicht
   (Form- und Medienbegriffe wie „Video“, „Vorlesung“). Nur Begriffe mit
   mindestens zwei Stimmen für Hochschule werden Themen; die übrigen stehen mit Stimmen und Begründung in
   `gnd_ausgeschlossen.csv` und sind für andere Bildungsbereiche nachnutzbar.
4. `gnd_kandidaten`, `gnd_faecher`, `gnd_lehrebene`, `gnd_zusammenfuehren`: wie bei OpenAlex. Hinweise
   für die Kandidaten kommen aus der Brücke und aus den Fächern der WLO-Belege; die Lehrebene sieht die
   Katalogzahlen als Hinweis. Unter `belege` stehen die Katalogzahlen (Titel, Lehrbücher,
   Hochschulschriften) mit Abfrage-Link und gegebenenfalls der WLO-Beleg; unter
   `mapping.hochschulrelevanz` die Stimmen. Unsichere Fälle stehen in `pruefliste_gnd.csv`.

**WLO-Keywords** (Folge `wlo`, Option `--wlo <export.csv> …`). Aus Exporten der WLO-Redaktion werden nur
Keywords, Hochschulfächer und, falls vorhanden, die Node-ID gelesen. Hochschulfächer sind die URIs der
Hochschulfächersystematik in `ccm:taxonid` (dort stehen auch Schulfächer) und in
`ccm:oeh_taxonid_university`. Gezählt wird je Keyword, in wie vielen Datensätzen es mit welchem Fach
vorkommt; berücksichtigt werden Keywords ab drei Datensätzen. Der Export und diese Zählung bleiben lokal
(`data/cache`). Ins Repo gelangen nur Keywords, die exakt einem GND-Sachschlagwort (GND-Themen) oder einem
Label bzw. Keyword von höchstens drei OpenAlex-Themen entsprechen (`wlo_belege`), jeweils als Beleg mit
Zahl der Datensätze, häufigsten Fächern und, wo vorhanden, Links auf die Nodes. Bei OpenAlex-Themen wird
zusätzlich geprüft, ob ein WLO-Fach im selben Studienbereich liegt wie das abgestimmte Fach
(`belege.wlo.fach_passt`: feinste übereinstimmende Ebene `studienfach`, `studienbereich`, `faechergruppe` oder `nein`; WLO-Fächer stehen oft nur auf grober Ebene), eine unabhängige Gegenprobe der Fachzuordnung.

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
| `synth:seed_parameter` | Gleichverteilungsfeld, Gruppe, Zufalls-Seed, Themenauswahl | automatisch |

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

**Auswahl der Themen.** Welche Themen in die Seeds eingehen, ist wählbar (CLI `--auswahl`, Oberfläche
„welche Themen einbezogen werden“; `python -m sem auswahl hochschule` zeigt Filter und Themenzahl):

| Filter | Werte | Voreinstellung |
|---|---|---|
| `quellen` | `openalex`, `gnd` | beide |
| `fach_status` | `gesichert`, `wahrscheinlich`, `unsicher`, `redaktionell` | ohne `unsicher` |
| `lehrebene_status` | wie `fach_status` | ohne `unsicher` |
| `hochschulrelevanz` (nur GND) | `gesichert`, `wahrscheinlich`, `unsicher` | ohne `unsicher` |
| `belege` (mindestens einer) | `lehrbuch` (≥ 1 Lehrbuch im hbz-Katalog), `hochschulschrift` (≥ 3), `wlo` (WLO-Keyword mit passendem Hochschulfach), `handmapping` (Fach durch Handmapping bzw. Sachgruppen-Brücke bestätigt) | keine Anforderung |

`unsicher` bleibt standardmäßig draußen, bis die Prüfliste durchgesehen ist; eine Korrektur macht ein
Thema `redaktionell`, und es ist wieder dabei. Mit der Voreinstellung sind es 9.070 von 9.233 Themen.
Jeder Seed trägt in `synth:provenance` → `synth:thema` den Status je Zuordnung und seine Belege:
Katalogzahlen mit Abfrage-URI (lobid), WLO-Datensätze mit Verweis auf WirLernenOnline.de und, wo der
Export sie enthält, Links auf die Node-IDs. Von WLO werden nur Keywords in Verbindung mit
Hochschulfächern genutzt. Die `.stats.json` zählt Themenquellen, Status und Belege.

**Reproduzierbarkeit.** Gleiche Mappings, gleiche Einstellungen und gleicher Zufalls-Seed ergeben
identische Seeds. Neben jeder Seed-Datei liegt eine `.stats.json` mit Parametern und Verteilungen.

## Materialien

Pro Seed bis zu sechs LLM-Aufrufe (OpenAI-kompatible API, Standardmodell `gpt-6-luna`):

1. **Volltext** aus dem Seed: `volltext_system.md` und `volltext.md`, etwa eine A4-Seite
   (400–550 Wörter). Kürzere Texte werden verworfen und neu angefragt.
2. **Titel, Beschreibung, Keywords**, je ein Aufruf in dieser Reihenfolge. Alle nutzen denselben
   Systemprompt (`system.md`) und dieselbe Materialvorlage (`material.md`): Volltext, die festen
   Metadaten des Seeds (Bildungsstufe, Fach, Zielgruppe, Sprache, Materialart) und die schon
   erzeugten Felder; noch leere Felder stehen als „-“. Den Auftrag liefert die Felddatei
   (`titel.md`, `beschreibung.md`, `keywords.md`). Thema, Themenbeschreibung und Seed-Keywords sieht
   das LLM hier nicht; die Felder werden also aus dem Text abgeleitet, wie es ein Modell
   „Text → Metadaten“ später lernen soll. Antworten werden geprüft (Titel 10–120 Zeichen,
   Beschreibung 250–1.200, mindestens 4 Keywords, höchstens 8 übernommen); ein unbrauchbares Feld
   wird bis zu dreimal neu angefragt, ohne den Volltext neu zu erzeugen.
3. **Inhaltstyp-Prüfung** (`inhaltstyp.md`, abschaltbar): Das LLM bestimmt die Materialart aus dem
   fertigen Material, ohne den Seed-Wert zu sehen, und wählt aus den Inhaltstypen des Bereichs. Das
   Ergebnis steht in `synth:inhaltstyp_pruefung` (erwartet, erkannt, Übereinstimmung) und dient als
   Qualitätsmaß; `ccm:oeh_lrt` bleibt der Seed-Wert.
4. **Frage-Antwort-Paare** (`fragen_antworten.md`, JSON): Ziel 10 Paare mit Lernzielstufe nach
   Bloom/Anderson, mindestens 8.

Die Prompts für Titel, Beschreibung, Keywords und Inhaltstyp sowie der gemeinsame Systemprompt sind
aus der WLO-Metadatenanreicherung übernommen und an die synthetischen Materialien angepasst.

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
| `rolle`, `sprache` | Autorenrolle im Volltext-Prompt, Sprache als Wort |
| `titel`, `beschreibung`, `keywords`, `volltext`, `auftrag` | nur in `material.md`: bisher erzeugte Felder („-“ = leer), Volltext, Auftrag aus der Felddatei |
| `kategorien`, `beispiel_id` | nur in `inhaltstyp.md`: Liste „Name → id“ der Inhaltstypen |

## Bekannte Grenzen

- OpenAlex-Themen stammen aus der Forschungsliteratur. Medizin, Biologie und Ingenieurwesen sind dort
  stark, Philologien, Kunst und Musik schwach vertreten. Die GND-Themen und die Gleichverteilung
  nach Fach gleichen das teilweise aus.
- LLM-Zuordnungen sind nicht deterministisch. Die Mehrfachabstimmung macht die Schwankung sichtbar
  (Status und Konfidenz), beseitigt sie aber nicht. Drei Stimmen desselben Modells können sich
  gemeinsam irren; Embedding-Ähnlichkeit und Handmapping dienen als unabhängige Gegenprobe.
- Die Lehrebene ist ungleich verteilt (94 grundlagen, 2.810 vertiefung, 1.612 forschung). Weil Forschungs-
  themen im 1.–2. Studienjahr ausgeschlossen sind, ist dieses Niveau bei Gleichverteilung nach Thema
  seltener (bei 30.000 Seeds rund 22 % statt 33 %). Die GND-Themen aus Lehrbüchern gleichen das aus.
- Synthetische Texte können fachliche Fehler enthalten. Für Trainingszwecke ist die Abbildung Text →
  Metadaten wichtiger als die fachliche Tiefe. Für eine Veröffentlichung als Lernmaterial sind sie nicht
  geprüft.

## Neuen Bildungsbereich anlegen

1. `sem/bereiche/<bereich>/__init__.py` mit einer Klasse, die `Bildungsbereich` implementiert
   (`topics`, `balance_key`, `make_seed`, `prompt_values`), und in `sem/bereiche/__init__.py`
   registrieren. Optional: `auswahl_optionen` und `passt` für wählbare Filter (Status, Quellen,
   Belege), `inhaltstyp_kategorien` für die Inhaltstyp-Prüfung.
2. Mappings unter `data/mappings/<bereich>/` ablegen, gleiche Themenstruktur wie bei Hochschule
   (`id`, `label_de`, Fachfelder mit `uri`/`label`, `quelle`, `mapping`).
3. Zum Bauen der Mappings `mapping_schritte()` und `mapping_folge` setzen. CLI und Oberfläche zeigen die
   Schritte dann automatisch. Die Bausteine in `sem/mapping/` sind bereichsunabhängig: Embedding-
   Kandidaten (`embeddings.py`), Mehrfachabstimmung mit Belegen (`abstimmung.py`), Prüfliste und
   Korrekturen (`pruefung.py`). Ein Schulbereich würde etwa Themen auf Schulfächer, ein
   Berufsbildungsbereich ESCO-Kompetenzen auf Berufe abstimmen.
4. Optional eigene Prompts unter `prompts/<bereich>/`.
