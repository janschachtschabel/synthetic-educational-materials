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
| Destatis-Systematik der Fächergruppen, Studienbereiche und Studienfächer | Ursprung der Hochschulfächersystematik; ausgelesen aus dem OpenEduHub-Vokabular `hochschulfaechersystematik`, nicht direkt bei Destatis | „Quelle: Statistisches Bundesamt (Destatis)“, steht im Vokabular (`quellenangabe`) und in der Herkunft jedes Seeds |
| [OpenAlex Topics](https://github.com/ourresearch/openalex-topic-classification) (4.516 Themen mit Beschreibung und Keywords) | Themen Hochschule (Vertiefung/Forschung) | CC0 1.0 |
| [Gemeinsame Normdatei (GND)](https://lobid.org/gnd) über lobid-gnd, [GND-Sachgruppen](https://d-nb.info/standards/vocab/gnd/gnd-sc.html) | GND-Themen Hochschule (Label, Synonyme, Oberbegriffe, Sachgruppe) | CC0 1.0 |
| [hbz-Verbundkatalog](https://lobid.org/resources) über lobid-resources | Kandidaten und Belege: Häufigkeit eines Schlagworts in Lehrbüchern und Hochschulschriften | CC0 1.0 |
| [WirLernenOnline](https://wirlernenonline.de) (Export der Redaktion) | nur Keywords in Verbindung mit Hochschulfächern, als Kandidaten und Belege; keine Titel, Beschreibungen oder Inhalte | Keywords nur als Häufigkeiten bzw. Abgleich auf GND/OpenAlex |
| [KIM-Bildungsstufen](https://w3id.org/kim/educationalLevel/) | `exactMatch` der Niveaustufen | CC0 1.0 |
| [ESCO](https://esco.ec.europa.eu) über die öffentliche API (2.938 Berufe, 12.564 Kompetenzen, deutsche Labels und Beschreibungen) | Berufe und Kompetenzen Berufsbildung, ESCO-Original-URIs | EU-Weiterverwendung (Beschluss 2011/833/EU), „Quelle: ESCO, Europäische Kommission“ in jeder Herkunftsangabe |
| OEH-Vokabulare `oehTopics` (Themenbäume der WLO-Fachportale, 3.182 Themen) und `discipline` | Themen und Fächer Schule, Fächer-closeMatch Elementar | CC0 1.0 |
| OEH-Vokabulare `kldb` (KldB 2010, Fassung 2020, Ebenen 1–4 mit Kurzbeschreibungen), `escoSkills`, `dqrCompetenceLevels` | Berufsfeld (wie WLO-Feld `ccm:oeh_profession_group`), Abgleich der Kompetenzen, DQR-Niveaus | CC0 1.0; KldB: „Quelle: Statistik der Bundesagentur für Arbeit“ |
| Gemeinsamer Rahmen der Länder für die frühe Bildung in Kindertageseinrichtungen (Fassung 2022, [nach Eurydice](https://eurydice.eacea.ec.europa.eu/de/eurypedia/germany/leitlinien-der-fruehkindlichen-bildung-betreuung-und-erziehung)) | sieben Bildungsbereiche Elementar | amtliches Werk; Liste und Kurzbeschreibungen im Repo CC0 |
| [Volkshochschul-Statistik des DIE](https://www.die-bonn.de/weiterbildung/statistik/vhs-statistik/) (Fachgebietsgliederung seit 2018, 7 Programmbereiche, 85 Fachgebiete) | Fach Erwachsenenbildung | Statistikbericht CC BY 4.0 DE; Codes und Bezeichnungen als Fakten; „Quelle: Deutsches Institut für Erwachsenenbildung (DIE), Volkshochschul-Statistik“ in jeder Herkunftsangabe |
| [Rahmencurriculum für Integrationskurse](https://www.goethe.de/resources/files/pdf209/rahmencurriculum-integrationskurs_20171.pdf) (Goethe-Institut, 2017) | Sprachkursthemen Erwachsenenbildung (Gliederung) | urheberrechtlich geschützt; nur Kapitelcodes und -titel übernommen; Quellenangabe (Titel, Ausgabe 2017, Goethe-Institut im Auftrag des BMI, hrsg. vom BAMF, URL) in jeder Herkunftsangabe |
| [Umsteigeschlüssel KldB 2020 → ISCO-08](https://statistik.arbeitsagentur.de/DE/Navigation/Grundlagen/Klassifikationen/Klassifikation-der-Berufe/KldB2010-Fassung2020/Arbeitsmittel/Arbeitsmittel-Nav.html) (BA, Stand 06.08.2021) | Handmapping-Hinweise ESCO-Beruf → KldB und Anforderungsniveaus | „Quelle: Statistik der Bundesagentur für Arbeit“; nur im Zwischenspeicher |

Die Vokabulare liegen aufbereitet unter `data/vocabs/` (Abruf mit `scripts/fetch_vocabs.py`, Datum in
der Datei).

## Mappings Hochschule

**Fächer.** Die Fächer folgen der Destatis-Systematik (Quelle: Statistisches Bundesamt (Destatis)), ausgelesen
aus der OEH-Fassung
(`http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/…`): Fächergruppe → Studienbereich →
Studienfach. Jedes Thema bekommt ein bis drei Studienfächer. Studienbereich und Fächergruppe werden daraus
abgeleitet.

Alle Themen stehen in einem Gesamtset, `themen_hochschule.jsonl` (9.130 Themen: 4.514 aus OpenAlex,
4.616 aus GND). Jedes Thema trägt seine Herkunft (`art`, `quelle`), Status und Belege; die Seeds lassen
sich danach filtern (siehe „Auswahl der Themen“). Die Teilergebnisse je Quelle entstehen beim Bauen im
Zwischenspeicher; der Schritt `themen` führt sie zusammen und läuft nach jeder Auswahl von Schritten zuletzt.

**Themen aus OpenAlex** (Folge `openalex`). Jeder Schritt ist einzeln aufrufbar und fortsetzbar
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
   `korrektur_lehrebene`; `-` schließt das Thema aus) und die Datei als `korrekturen.csv` speichert,
   überschreibt beim nächsten Zusammenführen das LLM-Ergebnis. Eine Zeile mit den bisherigen Codes
   bestätigt das Ergebnis. Der Schritt `korrekturen` wendet die Datei direkt auf die fertigen
   Themendateien an, ohne die Zwischenergebnisse (Stimmen, Kandidaten) zu brauchen.

   **Durchsicht (29.10.2026).** Beide Prüflisten (101 OpenAlex-Themen, 352 GND-Begriffe) sind
   redaktionell durchgesehen, das Ergebnis steht mit Kommentar in `korrekturen.csv`: 101 Ergebnisse
   bestätigt, 249 Fächer oder Lehrebenen korrigiert, 103 Themen ausgeschlossen. Ausgeschlossen wurden vor
   allem GND-Begriffe, die zu allgemein oder mehrdeutig für ein Lehrmaterial sind („Allgemeiner Teil“,
   „Erweiterung“, „List“), Einrichtungen und Personengruppen („Universität“, „Student“) sowie zwei
   unspezifische OpenAlex-Themen. Korrigiert wurden häufig Ersatzzuordnungen aus dem Embedding (etwa
   organische Chemie auf „Holz-/Fasertechnik“) und Fachdidaktik- oder Hochschullehre-Themen.

Seeds übernehmen `status` und `konfidenz` in die Herkunftsangabe des Fachs.

Stand der mitgelieferten Datei (gpt-6-luna, multilingual-e5-small, drei Stimmen, 4.516 Themen):
vor der Durchsicht Fach 4.205 gesichert, 297 wahrscheinlich, 14 unsicher; Lehrebene 3.436 gesichert,
1.079 wahrscheinlich, 1 unsicher; Handmapping bei 3.629 Themen bestätigt; 101 Themen auf der Prüfliste.
Sechs Themen erhielten in keiner Stimme ein Fach; sie trugen das ähnlichste Fach laut Embedding
(`mapping.ersatz`). Nach der Durchsicht: 4.514 Themen, Fach 4.205 gesichert, 210 wahrscheinlich,
99 redaktionell, kein Thema mehr unsicher. GND nach der Durchsicht: 4.616 Themen, Fach 4.123 gesichert,
242 wahrscheinlich, 251 redaktionell. Zusammen decken die Themen 256 von 273 Studienfächern ab.

**Themen aus GND und Katalog** (Folge `gnd`). OpenAlex beschreibt Forschungsfelder;
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

## Mappings Berufsbildung

**Themen** sind Kombinationen aus einem ESCO-Beruf und einer Kompetenz, die laut ESCO an diesem Beruf
hängt (essenziell oder optional). ESCO hat 2.938 Berufe mit im Mittel 18 essenziellen und 16 optionalen
Kompetenzen; das ergibt rund 60.000 essenzielle und 54.000 optionale Beruf-Kompetenz-Paare. Berufe und
Kompetenzen tragen ihre ESCO-Original-URIs (`http://data.europa.eu/esco/…`) mit deutschem Label und
Beschreibung, unverändert aus ESCO.

**Berufsfeld** ist die Klassifikation der Berufe (KldB 2010, Fassung 2020) aus dem OEH-Vokabular `kldb`,
das WLO für `ccm:oeh_profession_group` nutzt: Berufsbereich (10) → Berufshauptgruppe (37) → Berufsgruppe
(144) → Berufsuntergruppe (703). Jeder Beruf bekommt ein bis zwei Berufsuntergruppen; die oberen Ebenen
werden abgeleitet.

Schritte (`python -m sem mappings berufsbildung --liste`), alle fortsetzbar:

1. `esco`: Berufe und alle verknüpften Kompetenzen über die ESCO-API laden (Zwischenspeicher
   `data/cache/esco/`). Vier Berufe und fünf Kompetenzen liefert die API mit Serverfehler nicht aus; sie
   fehlen.
2. `hinweise`: Der Umsteigeschlüssel der Bundesagentur für Arbeit ordnet jedem KldB-5-Steller ISCO-08-
   Gruppen zu. Umgekehrt gelesen ergibt das je ISCO-Gruppe die passenden KldB-Berufsuntergruppen und
   die Anforderungsniveaus (5. Stelle: Helfer, Fachkraft, Spezialist, Experte). Über den ISCO-Code des
   ESCO-Berufs wird das zum Handmapping-Hinweis.
3. `kandidaten`: Lokale Embeddings vergleichen Bezeichnung, Synonyme und Beschreibung des Berufs mit
   Label, Definition und typischen Tätigkeiten aller 703 Berufsuntergruppen. Kandidaten: die 15
   ähnlichsten plus bis zu vier aus dem Umsteigeschlüssel.
4. `kldb`: drei Stimmen je Beruf (drei Perspektiven: KldB-Codierung, Berufsberatung, Arbeitsmarktstatistik)
   wählen ein bis zwei Untergruppen nach der berufsfachlichen Tätigkeit, mit Begründung und Belegbegriffen.
5. `qualifizierung`: drei Stimmen je Beruf zum üblichen Weg in den Beruf in Deutschland: `ausbildung`
   (anerkannter Ausbildungsberuf, dual oder schulisch), `fortbildung` (Aufstiegsfortbildung nach einer
   Ausbildung), `studium` oder `anlernen`. Als Hinweis sehen die Stimmen die KldB-Anforderungsniveaus der
   ISCO-Gruppe.
6. `wlo` (optional, `--wlo export.csv`): Keywords und Berufsgruppen der WLO-Datensätze mit Bildungsstufe
   Berufliche Bildung oder Fortbildung. Exakt gleiche Labels werden als Beleg an Kompetenzen und Berufe
   gehängt (Zahl der Datensätze, Node-IDs, KldB-Berufsgruppen der Datensätze). Titel, Beschreibungen und
   URLs werden nicht gelesen; der Export bleibt lokal.
7. `zusammenfuehren` (läuft zuletzt): Auswertung wie bei Hochschule (Status, Konfidenz,
   `handmapping_bestaetigt` = gewählte Untergruppe steht im Umsteigeschlüssel), `berufe.jsonl`,
   `kompetenzen.jsonl` und `pruefliste_berufe.csv`. `korrekturen.csv` hat Vorrang (Spalten
   `korrektur_codes` = KldB-Kennziffern, `-` = Beruf ausschließen, `korrektur_qualifizierung`).

Die Kompetenzen werden mit dem OEH-Vokabular `escoSkills` abgeglichen (`skos:exactMatch` auf die
ESCO-URI): 12.323 von 12.539 Kompetenzen sind dort enthalten; deren OEH-URI steht als `exactMatch` im
Seed, maßgeblich bleibt die ESCO-URI. 438 Kompetenzen haben einen WLO-Beleg.

**Redaktionelle Durchsicht.** Alle 418 Berufe der Prüfliste wurden durchgesehen (302 wichen vom
Umsteigeschlüssel ab, 99 ohne Mehrheit für die KldB, 18 ohne Mehrheit für den Qualifizierungsweg).
Ergebnis in `korrekturen.csv` mit Kommentar und ursprünglichem Prüflistengrund: 327 bestätigt, 86
korrigiert (KldB-Untergruppe und/oder Qualifizierungsweg, z. B. Import-/Exportmanager → Kaufleute im
Groß- und Außenhandel, Fortbildung; Fluglotse → Ausbildung; Lebensmittel-Anlagenberufe aus fachfremden
Untergruppen), 5 ausgeschlossen (Wahlbeobachter, Polygrafiesachverständiger, Grafologe, Medium,
Pelztierzüchter), dazu die drei Ausschlüsse von vorher (Escort, Wahrsager, Astrologe).

**Ergebnis** (2.930 Berufe): KldB gesichert 2.104, wahrscheinlich 413, redaktionell 413; in 2.039 Fällen
bestätigt der Umsteigeschlüssel die gewählte Untergruppe. Qualifizierung: Ausbildung 1.182, Fortbildung
452, Studium 794, Anlernen 502 (gesichert 2.009, wahrscheinlich 508, redaktionell 413). Die Prüfliste ist
leer. Mit der Voreinstellung (Ausbildung und Fortbildung, essenzielle Kompetenzen ohne
Querschnittskompetenzen, ohne `unsicher`) gehen 33.650 von 113.459 Beruf-Kompetenz-Themen in die Seeds
ein.

**Inhaltstypen** (`inhaltstypen.json`): 18 Typen aus OEH `new_lrt` mit Textsorten für die Berufsbildung,
z. B. Lernsituation nach der vollständigen Handlung (als „Fallstudie“), Arbeitsanleitung, Checkliste,
Prüfungsaufgaben, Unterweisungsentwurf nach der Vier-Stufen-Methode. Jeder Typ ist auf Kompetenzarten
(Kenntnisse bzw. Fähigkeiten) und Zielgruppen beschränkt.

**Niveaus** (`niveaus.json`) nach DQR mit `exactMatch` auf das OEH-Vokabular `dqrCompetenceLevels`:
Anlernen (2), Ausbildung 1. Jahr (3), Ausbildung 2. und 3. Jahr (4), Fortbildung Berufsspezialist (5),
Bachelor Professional (6), Master Professional (7). Gezogen wird nur, was zum Qualifizierungsweg passt;
die Bildungsstufe folgt daraus (Berufliche Bildung bzw. Fortbildung).

## Mappings Elementarbereich

**Bildungsbereiche** sind die sieben des Gemeinsamen Rahmens der Länder für die frühe Bildung in
Kindertageseinrichtungen (Fassung 2022, nach Eurydice): Alltagsintegrierte sprachliche Bildung; personale
und sozial-emotionale Entwicklung, Werte, Religiosität; MINT; Medien und digitale Bildung; Ästhetische
Bildung; Körper, Bewegung, Gesundheit, Prävention; Umweltbildung und BNE (`bildungsbereiche.json` mit
Kurzbeschreibung und `closeMatch` auf OEH-Schulfächer, mit denen WLO Kita-Materialien erschließt).

**Themen** sind GND-Sachbegriffe (`python -m sem mappings elementar --liste`):

1. `kandidaten_llm`: je Bildungsbereich 60–80 typische Kita-Themen aus drei Perspektiven (Erzieherin,
   Fachberatung, Material für Eltern), zusammen 1.374 Nennungen.
2. `wlo` (`--wlo export.csv`): Keywords der WLO-Datensätze mit Bildungsstufe Elementarbereich, die in
   mindestens zwei Datensätzen vorkommen (4.122).
3. `gnd`: alle 4.626 Kandidaten exakt auf GND-Sachbegriffe (lobid), nur Sachbegriffe: 1.462.
4. `eignung`: drei Stimmen je Begriff: Thema für Angebote mit Kindern (`kinderthema`), Thema für
   Fachkräfte und Eltern (`fachthema`) oder ungeeignet (zu speziell, nicht kindgerecht, ohne Bezug).
5. `bereiche`: drei Stimmen, ein bis zwei Bildungsbereiche je geeignetem Thema.
6. `alter`: drei Stimmen, unter 3 (`u3`), ab 3 (`ue3`) oder beide Altersgruppen.
7. `zusammenfuehren`: `themen_elementar.jsonl`, `pruefliste_themen.csv`; `korrekturen.csv` hat Vorrang
   (`korrektur_codes`: Bildungsbereich-IDs und optional die Themenart, `-` = ausschließen;
   `korrektur_alter`).

**Ergebnis:** 1.033 Themen (874 für Angebote mit Kindern, 159 für Fachkräfte und Eltern), 416 Begriffe
als ungeeignet verworfen. Je Bildungsbereich 51 (Medien) bis 280 (personal-sozial) Themen; Altersgruppe
meist „beide“ (581) oder „ab 3“ (439), nur 13 ausdrücklich „unter 3“. Herkunft: 664 nur WLO, 200 nur
LLM, 169 beides; 833 Themen haben einen WLO-Beleg. Die 34 Prüffälle sind redaktionell entschieden (23
bestätigt oder korrigiert, 11 ausgeschlossen, z. B. Methoden statt Themen und zu allgemeine Begriffe).

**Inhaltstypen** (`inhaltstypen.json`, 14 aus OEH `new_lrt`): Aktivitäts- und Projektidee, Forscher-
experiment (nur MINT und Umwelt), Bastelanleitung, Spielidee, Lied/Fingerspiel/Reim, Vorlesegeschichte,
Rollenspiel, Angebotsplanung, Beobachtungsbogen, Elternbrief, Fachbeitrag, Anleitung, Podcast. Jeder Typ
ist auf Themenart und Zielgruppe beschränkt. **Niveaus**: Krippe (unter 3), Kindergarten (3–6),
Vorschuljahr (5–6), passend zur Altersgruppe; `exactMatch` KIM-Bildungsstufe 0.

## Mappings Schule

Ein Bereich für die allgemeinbildende Schule; die **Schulstufe** (Primarstufe, Sekundarstufe I, II) ist
Metadatum des Seeds (`ccm:educationalcontext`) und über den Filter `stufen` wählbar
(z. B. `--auswahl schule.stufen=sek2` für reine Oberstufen-Seeds).

**Themen** sind die Themenbäume der 29 WLO-Fachportale (OEH-Vokabular `oehTopics`, „Taxonomie von
Lehrplanthemen in WirLernenOnline“, CC0): 3.182 Themen mit Fachportal, Pfad, Definition, Keywords und
`relatedMatch` auf OEH-Schulfächer. WLO erschließt Materialien mit diesen URIs im Feld `ccm:curriculum`;
die Seeds tragen sie dort ebenfalls.

Schritte (`python -m sem mappings schule --liste`):

1. `themen`: Themenbäume laden (ohne die Fachportale selbst).
2. `wlo` (`--wlo export.csv`): je Thema die Bildungsstufen der WLO-Datensätze, die es in `ccm:curriculum`
   tragen (Zahl der Datensätze je Stufe, Node-IDs). Eine Stufe gilt als belegt, wenn sie in mindestens 20 %
   von mindestens drei Datensätzen vorkommt (2.646 Themen).
3. `gnd`: Themenlabel exakt auf GND-Sachbegriffe (954 Treffer), als zusätzliche Normierung (`gnd`).
4. `art`: drei Stimmen: Unterrichtsthema, Thema für Lehrkräfte (Didaktik, Methoden, Schulentwicklung)
   oder Sammelknoten ohne eigenen Inhalt (verworfen).
5. `stufen`: drei Stimmen, in welchen Schulstufen das Thema laut Lehrplänen unterrichtet wird (bis zu drei).
   Die WLO-Stufen sieht das LLM nicht; sie dienen als unabhängige Gegenprobe (`wlo_bestaetigt`).
6. `zusammenfuehren`: `themen_schule.jsonl`, Prüfliste (keine Mehrheit, Abweichung von WLO, Ersatz aus
   WLO); `korrekturen.csv` hat Vorrang (`korrektur_codes` = Stufen, `-` = ausschließen; `korrektur_art`).

**Ergebnis:** 3.153 Themen aus 29 Fachportalen (3.030 Unterrichtsthemen, 123 für Lehrkräfte), 27
Sammelknoten verworfen. Stufen: Primarstufe 995, Sek I 2.763, Sek II 2.419 Themen; Status gesichert 3.047,
wahrscheinlich 85, redaktionell 21. In 2.191 Fällen bestätigen die WLO-Datensätze mindestens eine der
gewählten Stufen. Die 22 Prüffälle sind redaktionell entschieden (OER- und Schulentwicklungsthemen als
Lehrkräftethemen für alle Stufen, Pädagogik als Oberstufenfach, ein Hochschulthema ausgeschlossen).

**Inhaltstypen** (`inhaltstypen.json`, 19 aus OEH `new_lrt`): u. a. Arbeitsblatt, Übungsmaterial,
Erklärvideo-Transkript, Schulbuchkapitel, Merkblatt, Tafelbild, Klassenarbeit, Erwartungshorizont, Quiz,
Lernpfad, Stationenlernen, Versuchsanleitung (nur Naturwissenschaften und Geographie), Stundenentwurf,
Unterrichtsreihe, Fachbeitrag für Lehrkräfte; je Typ Themenart, Zielgruppe, Stufen und Fachportale.
**Niveaus**: Klasse 1–2, 3–4, 5–6, 7–8, 9–10, Einführungs- und Qualifikationsphase, `exactMatch`
KIM-Bildungsstufen 1–3.

## Mappings Erwachsenenbildung

Allgemeine und berufliche Weiterbildung in Kursform (Volkshochschule u. a.); die berufliche
Aufstiegsfortbildung (Meister, Fachwirt) gehört zur Berufsbildung.

**Fach** sind die 7 Programmbereiche und 85 Fachgebiete der Volkshochschul-Statistik des DIE
(Fachgebietsgliederung seit Berichtsjahr 2018; `programmbereiche.json`), z. B. 1.04 Globales Lernen/BNE,
3.05 Essen und Trinken, 4.06 Englisch, 5.04 Kaufmännische Lehrgänge, 6.01 Hauptschulabschluss,
7.01 Alphabetisierung. Die Sammelpositionen x.00 sind kein Zuordnungsziel. Die 24 Fremdsprachen sind für die
Themen ein gemeinsames Ziel „4.fs“, weil Sprachkursthemen für alle Sprachen gelten; die konkrete Sprache wird
beim Seed gezogen (je Thema rotierend) und als Zielsprache in `cclom:general_language` ergänzt.

**Themen** aus drei Quellen (Filter `quellen`):

1. `gnd` (955): Schritte `kandidaten_llm` (je Fachgebiet 30–40 typische Kursthemen aus zwei Perspektiven),
   `gnd` (exakt auf GND-Sachbegriffe, 1.076 Treffer), `kandidaten` (je Begriff die sechs ähnlichsten
   Fachgebiete per Embedding plus die Fachgebiete, die ihn vorgeschlagen haben), `eignung` (drei Stimmen:
   Kursthema, Thema für Kursleitende, ungeeignet; 121 verworfen), `fachgebiete` (drei Stimmen, ein bis zwei
   Fachgebiete). WLO-Keywords der Erwachsenenbildung dienen nur als Beleg (`wlo`, 347 Themen), nicht als
   Kandidaten, weil sie stark mit anderen Bereichen vermischt sind.
2. `rahmencurriculum` (117): Sprachkursthemen aus der Gliederung des Rahmencurriculums für Integrationskurse
   (Goethe-Institut im Auftrag des BMI, 2017): die zwölf Handlungsfelder mit ihren Lernzielbereichen (z. B.
   „Wohnen: Mietverträge verstehen und abschließen“) und die fünf handlungsfeldübergreifenden Bereiche
   (`sprachkurs_themen.json`; übernommen sind nur Codes und Titel, Schreibung aus den Versalien per LLM
   wiederhergestellt). Alle für Deutsch als Fremdsprache, die alltagsnahen auch für Fremdsprachen. GND-Begriffe
   treffen solche Kommunikationssituationen kaum.
3. `schulthemen` (485): für die Schulabschlusskurse die Unterrichtsthemen des Bereichs Schule in Deutsch,
   Mathematik und Englisch (Sek I → Haupt- und Realschulabschluss, Sek II → Fachhochschulreife und Abitur).

**Ergebnis:** 1.557 Themen in 54 Fachgebieten (Programmbereiche: Politik–Gesellschaft–Umwelt 323,
Kultur–Gestalten 308, Gesundheit 103, Sprachen 146, Arbeitsleben–IT–Organisation 141, Schulabschlüsse 505,
Grundbildung 31). Die 19 Prüffälle sind redaktionell entschieden. Grundbildung hat wenige Themen; bei
Gleichverteilung nach Programmbereich wiederholen sie sich.

**Inhaltstypen** (14 aus OEH `new_lrt`): Kursbeschreibung, Handout, Übungsblatt, Anleitung, Checkliste,
Selbstlerneinheit, Präsentation, Quiz, Wortliste und Dialog/Rollenspiel (Sprachen, Grundbildung),
Übungsprüfung (Sprachen, Schulabschlüsse), Podcast, Kursablaufplan und Fachbeitrag für Kursleitende.
**Niveaus** je Programmbereich: GER A1–C1 (Sprachen), Alpha-Level 1–2 und 3–4 (Grundbildung), Vorbereitung
Haupt-, Realschulabschluss bzw. Hochschulreife (Schulabschlüsse), sonst Einsteiger und Fortgeschrittene.

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

`unsicher` bleibt standardmäßig draußen, bis die Prüfliste durchgesehen ist; eine Korrektur oder
Bestätigung macht ein Thema `redaktionell`, und es ist wieder dabei. Nach der Durchsicht sind mit der
Voreinstellung alle 9.130 Themen dabei.
Jeder Seed trägt in `synth:provenance` → `synth:thema` den Status je Zuordnung und seine Belege:
Katalogzahlen mit Abfrage-URI (lobid), WLO-Datensätze mit Verweis auf WirLernenOnline.de und, wo der
Export sie enthält, Links auf die Node-IDs. Von WLO werden nur Keywords in Verbindung mit
Hochschulfächern genutzt. Die `.stats.json` zählt Themenquellen, Status und Belege.

**Seeds Berufsbildung.** Statt `ccm:oeh_taxonid_university` tragen sie `ccm:oeh_profession_group`
(KldB-Berufsuntergruppen, OEH-URIs), dazu `synth:beruf` (ESCO-URI, Label, ISCO-08, Beschreibung,
Qualifizierungsweg, KldB-Oberebenen) und `synth:kompetenz` (ESCO-URI, Label, Beschreibung, Art,
Reichweite, essenziell/optional, `exactMatch` auf OEH `escoSkills`). `synth:thema` ist die Kompetenz im
Kontext des Berufs. Inhaltstyp passt zu Kompetenzart und Zielgruppe (80 % Lernende, 20 % Lehrende und
Ausbildende); das Niveau passt zum Qualifizierungsweg. Optional: `synth:lernort` (40 %; Berufsschule,
Ausbildungsbetrieb, überbetriebliche Ausbildungsstätte bzw. Fortbildungsorte) und
`synth:querschnittsthema` (25 %; eine der vier Standardberufsbildpositionen). Gleichverteilung: `beruf`
(Standard), `thema`, `kompetenz`, `berufsgruppe`, `berufshauptgruppe`, `berufsbereich`.

| Filter (Berufsbildung) | Werte | Voreinstellung |
|---|---|---|
| `qualifizierung` | `ausbildung`, `fortbildung`, `studium`, `anlernen` | Ausbildung und Fortbildung |
| `relation` | `essenziell`, `optional` | essenziell |
| `kompetenzart` | `wissen`, `faehigkeit` | beide |
| `reichweite` | `berufsspezifisch`, `branchenspezifisch`, `branchenuebergreifend`, `querschnitt` | ohne `querschnitt` |
| `kldb_status`, `qualifizierung_status` | wie `fach_status` | ohne `unsicher` |
| `belege` | `wlo` (Kompetenz als WLO-Keyword mit Datensätzen derselben KldB-Berufsgruppe), `wlo_beruf` (Beruf als WLO-Keyword), `handmapping` (KldB durch Umsteigeschlüssel bestätigt) | keine Anforderung |

Der Volltext-Prompt der Berufsbildung (`prompts/berufsbildung/volltext.md`) nennt Beruf, Kompetenz,
Berufsfeld, Lernort und Querschnittsthema und verlangt Bezug auf typische Arbeitsaufgaben im Beruf.

**Seeds Elementarbereich.** Bildungsstufe fest Elementarbereich; `synth:bildungsbereich_elementar`
(Bildungsbereich des Gemeinsamen Rahmens) und `ccm:taxonid` (closeMatch auf OEH-Schulfächer). Zielgruppe je
Themenart: Kinderthemen 65 % Fachkräfte, 20 % Eltern, 15 % Kinder (Vorlesegeschichte, Lied); Fachthemen
75 % Fachkräfte, 25 % Eltern. Optional: didaktische Funktion, Dauer, Sozialform (je 40 %). Gleichverteilung:
`bildungsbereich` (Standard), `thema`, `altersgruppe`, `art`. Filter: `art`, `herkunft` (`llm`, `wlo`),
`eignung_status`, `bereich_status`, `alter_status`, `belege` (`wlo`, `llm_bestaetigt`). Eigener
Volltext-Prompt (`prompts/elementar/volltext.md`): altersgerecht, ohne Tests und Noten, mit Hinweisen zu
Sicherheit und Vielfalt.

**Seeds Schule.** `ccm:educationalcontext` ist die Schulstufe: aus den Stufen des Themas (innerhalb des
Filters `stufen`) je Thema rotierend gezogen; das Niveau (Jahrgang) passt dazu. `ccm:taxonid` = Schulfach aus
dem Themenbaum, `ccm:curriculum` = Thema im WLO-Themenbaum. Fremdsprachenportale ergänzen die Zielsprache in
`cclom:general_language`. Zielgruppe: Unterrichtsthemen 75 % Lernende, 25 % Lehrkräfte; Lehrkräftethemen
nur Lehrkräfte. Optional: Unterrichtsphase (60 %), Dauer (50 %), Anforderungsbereich I–III der KMK (40 %,
in der Primarstufe nur I und II), Sozialform (30 %). Gleichverteilung: `thema` (Standard), `portal`
(kleine Portale wiederholen ihre Themen oft, „Gewaltprävention“ hat nur eines), `fach`, `stufe`. Filter:
`stufen`, `art`, `stufen_status`, `art_status`, `belege` (`wlo`, `wlo_stufe`, `gnd`). Eigener
Volltext-Prompt (`prompts/schule/volltext.md`) mit Themenpfad, Jahrgang, Operatoren und Zielsprache.

**Seeds Erwachsenenbildung.** Bildungsstufe fest Erwachsenenbildung; `synth:fachgebiet_vhs` und
`synth:programmbereich_vhs` (Volkshochschul-Statistik). Zielgruppe: Kursthemen 80 % Teilnehmende, 20 %
Kursleitende; Themen für Kursleitende nur Kursleitende. In der Grundbildung immer, sonst in 5 % der Seeds
`synth:sprachniveau` = einfache Sprache. Optional: Einsatz (50 %), Dauer (40 %), Veranstaltungsform (40 %;
Abendkurs, Wochenendseminar, Bildungsurlaub, Online-Kurs, Vortrag, Selbstlernangebot). Gleichverteilung:
`programmbereich` (Standard), `fachgebiet`, `thema`, `art`. Filter: `programmbereiche`, `quellen`, `art`,
`eignung_status`, `fachgebiet_status`, `belege` (`wlo`, `llm_bestaetigt`). Eigener Volltext-Prompt
(`prompts/erwachsenenbildung/volltext.md`) mit Zielsprache und einfacher Sprache.

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
   Bloom/Anderson, mindestens 8. Schritt 3 und 4 laufen gleichzeitig.

Jedes Material vermerkt in `synth:generierung` die Dauer und welche Felder neu angefragt werden mussten
(`neu_angefragt`). Die Fortschrittsanzeige zählt außerdem Wiederholungen ganzer Materialien
(z. B. nach Rate-Limit oder Zeitüberschreitung) und zeigt die letzte Meldung. Ein Material braucht fünf
aufeinanderfolgende Aufrufe; bei Reasoning-Modellen verkürzt ein geringerer Reasoning-Aufwand
(`--reasoning-effort`, Oberfläche „Reasoning-Aufwand“) die Laufzeit deutlich.

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
| `beruf`, `beruf_beschreibung`, `kompetenzart`, `lernort`, `querschnittsthema` | nur Berufsbildung (`lernort`, `querschnittsthema` optional) |
| `themenart`, `sozialform` | Elementarbereich (`sozialform` optional, auch Schule) |
| `thema_pfad`, `zielsprache`, `anforderungsbereich` | nur Schule (`anforderungsbereich` optional, `zielsprache` nur Fremdsprachen) |
| `programmbereich`, `einfache_sprache`, `veranstaltungsform` | nur Erwachsenenbildung (auch `zielsprache`) |
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
- ESCO ist europäisch. Viele Berufe entsprechen keinem deutschen Ausbildungsberuf eins zu eins, manche
  deutsche Übersetzungen sind holprig. Der Qualifizierungsweg ist eine Einschätzung per Abstimmung,
  keine amtliche Zuordnung zu den anerkannten Ausbildungsberufen (deren Verzeichnis ist nicht offen
  lizenziert). Lernfelder der Rahmenlehrpläne gibt es nicht maschinenlesbar; sie fehlen.
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
