"""Regensburger Verbundklassifikation (RVK): Gesamtabzug lesen und thematische Klassen auswählen.

Quelle: MarcXML-Gesamtabzug der Universitätsbibliothek Regensburg, CC0 1.0
(https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online/rvk-download), z. B.
rvko_marcxml_2026_3.xml.gz. Der Abzug (30 MB gepackt, 1,4 GB entpackt) bleibt lokal (data/lokal/rvk/);
ins Repo kommt nur die abgeleitete Auswahl (data/mappings/hochschule/rvk_klassen.jsonl).

Format (MARC 21 for Classification Data, https://rvk.uni-regensburg.de/api_2.0/marcxml.html): Feld 153 mit
Notation ($a, bei Bereichen $c), Benennung ($j) und dem Pfad der Oberklassen in aufsteigender Reihenfolge als
Gruppen $e(/$f) + $h; 7XX Registerbegriffe ($a, $0 = GND-ID, $2 gnd): 750 Sachschlagwort, 751 Geografikum,
700 Person, 710 Körperschaft, 711 Kongress, 730 Werk, 748 Zeitschlagwort.

Die Tiefe einer Klasse ist die Länge ihres Pfads plus eins (Ebene 1 = Fachsystematik wie Q, 2 = QP,
3 = QP 300 - QP 399 …), nicht die Länge der Notation. Die Auswahl „thematisch“ ist eine Heuristik über
Benennungen und Registerfelder; welche Regeln greifen, steht in typ() und in METHODIK.md.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import quote

NS = "{http://www.loc.gov/MARC21/slim}"
QUELLE_NAME = "Regensburger Verbundklassifikation (RVK)"
LIZENZ = "CC0-1.0"
DOWNLOAD = "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online/rvk-download"
# Begriffs-URIs wie in BARTOC/coli-conc (Namensraum http://rvk.uni-regensburg.de/nt/, Leerzeichen → _);
# die RVK selbst vergibt keine URIs, verlinkt wird zusätzlich auf RVK-Online
URI_BASIS = "http://rvk.uni-regensburg.de/nt/"
ONLINE = "https://rvk.uni-regensburg.de/regensburger-verbundklassifikation-online#notation/"
MAX_TIEFE = 5

# Ausgeschlossen, weil sie keine Fachgebiete, sondern Bibliotheksgruppen nach Form oder Region sind
# (Tiefe-2-Klassen; Begründung je Eintrag, auch in METHODIK.md)
AUSGESCHLOSSEN = {
    "AA": "Bibliografien der Bibliografien, Bibliothekskataloge (Formgruppe)",
    "AB": "Verzeichnisse amtlicher Druckschriften und Hochschulschriften (Formgruppe)",
    "AC": "Bibliografien besonderer Literaturgattungen (Formgruppe)",
    "AD": "Personal-, Landes- und topografische Bibliografien (Formgruppe)",
    "AE": "Enzyklopädien, allgemeine Nachschlagewerke (Formgruppe)",
    "AF": "Biografische Lexika (Formgruppe)",
    "AH": "Wörterbücher (Formgruppe, nach Sprachen gegliedert)",
    "AV": "Sammelwerke aus verschiedenen Gebieten, Serien (Formgruppe)",
    "AW": "Parlamentsschriften (Formgruppe)",
    "AX": "Akademieserien, Periodika wissenschaftlicher Gesellschaften (Formgruppe)",
    "AZ 10000": "Allgemeine und fachübergreifende Zeitschriften (Formgruppe)",
    "RC - RZ": "Regionale Geografie: dieselbe Gliederung der allgemeinen Geografie, je Land und Landschaft "
               "wiederholt (115.000 Klassen); die allgemeine Geografie (RB) bleibt",
}

# --- Heuristik --------------------------------------------------------------------------------
_KEY = re.compile(r"(?i)schl[üu]ssel(?!gewalt|roman|au\b)|geschl[üu]sselt|\(\+ ?\w*schl")
_KEY_AUSNAHME = re.compile(r"(?i)l[öo]sungsschl|schl[üu]sselliteratur|ohne schl[üu]sselung")
_PERSON_CONTAINER = re.compile(
    r"(?i)^(Einzelne (Autoren|Pers[öo]nlichkeiten|Personen|Germanisten|Anglisten|Romanisten|Slawisten|"
    r"Philosophen|Theologen|K[üu]nstler|Komponisten|Musiker|Maler|Architekten|Bildhauer|Wissenschaftler|"
    r"Gelehrte|Herrscher|Politiker|Schriftsteller|Dichter|Mathematiker|Physiker|Chemiker|Historiker|"
    r"Juristen|P[äa]dagogen|Psychologen|Soziologen|[ÖO]konomen|Volkswirte|Werke)|Einzelpers[öo]nlichkeiten|"
    r"Autoren( [A-Z]|$| und| der)|Gelehrte|Personenschl[üu]ssel|Biografien (und Autobiografien )?einzelner|"
    r"Einzelbiografien|Einzelne Autoren)")
# „Nachname, Vorname“ zählt nur unter Personen-Oberklassen („Künstler E“, „Maler und Grafiker“): ohne diese Bedingung
# fielen auch Sachbenennungen wie „Sprachverbreitung, Sprachberührungen“ heraus
_NAME = re.compile(r"^[A-ZÄÖÜ][\w'’\-]+(?: [a-zäöü]{1,4})?(?: [A-ZÄÖÜ][\w'’\-]+)?, [A-ZÄÖÜ][\w'’.\-]+"
                   r"(?: [A-ZÄÖÜ][\w'’.\-]+){0,3}(?: <[^>]*>| \([^)]*\))?$")
_PERSONEN_OBER = re.compile(r"(?i)k[üu]nstler|\bmaler\b|bildhauer|architekten|komponisten|\bmusiker\b|autoren|"
                            r"\bdichter\b|schriftsteller|philosophen|theologen|gelehrte|wissenschaftler\b|personen|"
                            r"biografien|pers[öo]nlichkeiten|herrscher|politiker\b|^[\wÄÖÜäöüß ]+ [A-Z](/[A-Z])?$")
_ZEIT = re.compile(
    r"(?i)^(ca\. )?(\d{1,2}\. ?(und \d{1,2}\. )?(Jahrhundert|Jh)|\d{1,2}\.-\d{1,2}\. Jh|Antike$|Mittelalter$|"
    r"Neuzeit$|Fr[üu]he Neuzeit$|Barock|Romantik$|Moderne$|Gegenwart$|Vor- und Fr[üu]hgeschichte$|"
    r"(bis|seit|ab|nach|vor) (ca\. )?\d{3,4}|\(?\d{3,4} ?[-–] ?\d{2,4}|\d{3,4}er Jahre|Zeitgeschichte$)")
# Form- und Sammellabels: zählen nur, wenn die ganze Benennung aus ihnen besteht (Teile getrennt durch Komma,
# Semikolon, „und“), damit „Allgemeines Verwaltungsrecht“ bleibt, „Allgemeines, Gesamtdarstellungen“ nicht
_FORMWORT = re.compile(
    r"(?i)(allgemeines|allgemein|allgemeine (literatur|darstellungen|werke|schriften|fragen|probleme)|"
    r"sonstige[sn]?( \w+)?|übrige[sn]?( \w+)?|weitere( \w+)?|verschiedenes|zeitschriften?|zeitungen|"
    r"bibliogra(f|ph)ien?|lehrbücher|lehrbuch|hand- und lehrbücher|handbücher|einführungen|einführende werke|"
    r"gesamtdarstellungen?|kongress(e|berichte)?|tagungsberichte|festschriften?|wörterbücher|nachschlagewerke|"
    r"lexika|lexikon|enzyklopädien|fachlexika|atlanten|dissertationen|aufsatzsammlungen|sammelwerke|"
    r"sammlungen|jahrbücher|kommentare|gesamtausgaben|teilausgaben|einzelausgaben|textausgaben|texte|"
    r"übersetzungen|briefe|primärliteratur|sekundärliteratur|werke|gesammelte werke|einzelschriften|"
    r"einzeldarstellungen|darstellungen|teilgebiete und einzelfragen|einzelfragen|einzelprobleme|"
    r"(einzelne|spezielle|sonstige|weitere|andere) (probleme|fragen|themen|einzelfragen|gebiete|aspekte)|"
    r"quellen|quellensammlungen|kompendien|repetitorien|monografien|serien|reihen|biografien|biografie|"
    r"fallstudien|forschungsberichte?|kataloge|verzeichnisse|periodika|programme|protokolle|gesetze|"
    r"entscheidungssammlungen|rechtsprechung|materialien|dokumentation|dokumente|bildbände|abbildungen|"
    r"aufsätze|reden|interviews|nachrufe|bibliografie, dokumentation|auszüge|führer|karten|"
    r"(ab|bis|vor|nach|seit) \d{4}|\d{4} ?- ?\d{0,4})")
_SAMMEL = re.compile(r"(?i)^(Einzelne |Unterteilung nach|Gliederung nach|Ordnung nach|Autoren( [A-Z]| und|$)|"
                     r"[A-Z]\.? |[A-Z]$)")
# Benennungen, die mit einem Formbegriff beginnen („Festschriften bei sachlichem Gesichtspunkt“, „Sammelwerke
# mehrerer Verfasser“, „Darstellungen nach Jahrhunderten“)
_FORM_ANFANG = re.compile(
    r"(?i)^(festschriften|sammelwerke|gesammelte|abgeschlossene|laufende|audiovisuelle medien|tabellenwerke|"
    r"kompendien|handbücher|fachlexika|übergreifende darstellungen|bibliografie\b|dokumentation\b|"
    r"tabellen\b|darstellungen? (nach|ohne|mit|von|aus|zu|in|über)\b|kongress|zeitschriften|bibliogra|lehrbücher|"
    r"wörterbücher|lexika|nachschlagewerke|quellenschriften|quellen (zu|zur|zum)|textsammlungen|sammlungen|"
    r"kataloge|bildbände|atlanten|aufsatzsammlungen|hochschulschriften|dissertationen|biografien|briefe|"
    r"gesamtdarstellungen|einführungen|studienliteratur|prüfungsliteratur|repetitorien|fallsammlungen|"
    r"entscheidungssammlungen|formularbücher|gesetzessammlungen|jahrbücher|verzeichnisse|adressbücher|"
    r"kommentare|allgemeine werke|allgemeine darstellungen|sammelschriften|einzelschriften|serien\b|"
    r"biografische nachschlagewerke|handschriftenkataloge|einzeldarstellungen|auszüge|führer\b|karten\b|"
    r"schulbücher|terminologische|bibliothekskataloge|alphabetische nachschlagewerke|zeichenlexika|"
    r"veranstaltungsjahr|abhandlungen$|meisterschaften$|teilgebiete und einzelfragen|darstellung ohne|"
    r"schriftenreihen|interpretationssammlungen|einzelbeiträge|besonderes$|besondere gesichtspunkte|"
    r"allgemeine sammlungen|regionale sammlungen|systematische spezifizierung|systematische einzelfragen|"
    r"literaturessays|antike welt$|fachwörterbücher|einzeluntersuchungen|darstellungen unter|"
    r"fest- und gedenkschriften|historische darstellungen|gesetzestexte|gesetzgebungsmaterialien|"
    r"etymologische wörterbücher|tagungs|tagungen|einzelwerke|anonyma|literarische zeitschriften|"
    r"schriften mehrerer|sammelbiografien|konferenzschriften|fortlaufende ressourcen|museumskurzführer|"
    r"matrikel|lehrkörperverzeichnisse|adressbücher|personal, preise)")
# Container, deren Unterklassen einzelne Orte, Institutionen oder Objekte sind („Einzelne Orte der Oberpfalz“,
# „Kataloge einzelner Museen“, „Orte und Landschaften B“)
_INSTANZ = re.compile(
    r"(?i)^(einzelne (orte|städte|länder|staaten|museen|institutionen|hochschulen|universitäten|bibliotheken|"
    r"archive|firmen|unternehmen|verlage|zeitungen|zeitschriften|kirchen|klöster|bauwerke|schulen|vereine|"
    r"parteien|organisationen|gesellschaften|akademien|regionen|landschaften|gemeinden|bistümer|diözesen|"
    r"handschriften|sammlungen|denkmäler|fundorte|grabungen|ausstellungen|festivals|theater|orchester)|"
    r"alphabetische (liste|ordnung)|orte\b|städte [A-Z]|kataloge einzelner|.*\b(orte|städte) [A-Z](/[A-Z])?$)")
_BUCHSTABE = re.compile(r"^[\wÄÖÜäöüß ]+ [A-Z](/[A-Z])?$")  # „Orte T“, „Autoren I/J“, „Gattungen und Formen B“
_ALPHA = re.compile(r"^[A-Za-zÄÖÜäöü]{1,3}\.? ?[-–/] ?[A-Za-zÄÖÜäöü]{1,3}\.?$")
_TEILE = re.compile(r"\s*(?:,|;|\bund\b|\bsowie\b|\(|\))\s*")


def ist_formlabel(label: str) -> bool:
    teile = [t for t in _TEILE.split(label.strip().rstrip(".:")) if t]
    return bool(teile) and all(_FORMWORT.fullmatch(t) for t in teile)


def notation(r: dict) -> str:
    return r["a"] + (f" - {r['c']}" if r.get("c") else "")


def uri(nt: str) -> str:
    return URI_BASIS + nt.replace(" ", "_")


def url(nt: str) -> str:
    return ONLINE + quote(nt)


def quartal(pfad: str | Path) -> str:
    """Quartalsstand aus dem Dateinamen, z. B. rvko_marcxml_2026_3.xml.gz → 2026_3."""
    m = re.search(r"(\d{4}_\d)", Path(pfad).name)
    return m.group(1) if m else ""


def lade(pfad: str | Path) -> list[dict]:
    """Alle Klassen des Abzugs: {"notation", "label", "pfad": [{"notation", "label"}], "tiefe", "register": [...]}.
    Doppelte Notationen (ein paar Hundert im Abzug) zählen einmal."""
    pfad = Path(pfad)
    opener = gzip.open if pfad.suffix == ".gz" else open
    out, seen = [], set()
    with opener(pfad, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag != NS + "record":
                continue
            r: dict = {"pfad": [], "register": []}
            for df in el.findall(NS + "datafield"):
                tag = df.get("tag")
                sfs = [(sf.get("code"), (sf.text or "").strip()) for sf in df]
                if tag == "153":
                    cur = None
                    for c, v in sfs:
                        if c in ("a", "c", "j"):
                            r[c] = v
                        elif c == "e":
                            cur = {"e": v}
                            r["pfad"].append(cur)
                        elif c in ("f", "h") and cur is not None:
                            cur[c] = v
                elif tag.startswith("7"):
                    d = {"tag": tag, **{c: v for c, v in sfs if c in ("a", "0")}}
                    r["register"].append(d)
            el.clear()
            if "a" not in r:
                continue
            nt = notation(r)
            if nt in seen:
                continue
            seen.add(nt)
            out.append({"notation": nt, "label": r.get("j", ""),
                        "pfad": [{"notation": p["e"] + (f" - {p['f']}" if p.get("f") else ""),
                                  "label": p.get("h", "")} for p in r["pfad"]],
                        "tiefe": len(r["pfad"]) + 1, "register": r["register"]})
    return out


def geo_begriffe(klassen: list[dict]) -> set[str]:
    return {x["a"] for k in klassen for x in k["register"] if x["tag"] == "751" and x.get("a")}


def ist_geo(label: str, geo: set[str]) -> bool:
    s = re.sub(r"^(ehem\.|ehemalige[sr]?|Allgemeines und) ", "", label.strip())
    s = re.sub(r" (insgesamt|allgemein)$", "", s)
    s = re.sub(r" \(.*\)$", "", s)
    if s in geo or label in geo:
        return True
    teile = [x.strip() for x in re.split(r",|;| und | / ", s) if x.strip()]
    return bool(teile) and all(x in geo for x in teile)


def typ(k: dict, geo: set[str]) -> str:
    """Art einer Klasse (Priorität in dieser Reihenfolge): ausgeschlossen, schluessel, person, werk, sammel,
    instanz, geografie, unter_geografie, zeit, form, thematisch."""
    j = k["label"]
    ober = [p["label"] for p in k["pfad"]]
    nts = [p["notation"] for p in k["pfad"]] + [k["notation"]]
    if any(n in AUSGESCHLOSSEN for n in nts):
        return "ausgeschlossen"
    if any(_KEY.search(h) and not _KEY_AUSNAHME.search(h) for h in ober + [j]):
        return "schluessel"
    tags = {x["tag"] for x in k["register"]}
    if any(_PERSON_CONTAINER.search(h) for h in ober) or "700" in tags \
            or (_NAME.search(j) and "750" not in tags and ober and _PERSONEN_OBER.search(ober[-1])):
        return "person"
    if "730" in tags or (j[:1].islower() and not j.startswith("e-")):
        return "werk"  # Werktitel und Anhängsel wie „zum Gesamtwerk“, „epistulae“
    if _PERSON_CONTAINER.search(j) or _SAMMEL.search(j) or _ALPHA.search(j) or _BUCHSTABE.search(j) \
            or j.startswith(("(", "s.", "s. ")) or len(j) < 3:
        return "sammel"  # auch Buchstabenbereiche wie „Orte T“, „Gattungen und Formen B“
    if any(_INSTANZ.search(h) for h in ober):
        return "instanz"
    if ist_geo(j, geo) or ("751" in tags and not (tags & {"750", "700", "710", "711", "730"})):
        return "geografie"
    if any(ist_geo(h, geo) for h in ober):
        return "unter_geografie"
    if _ZEIT.search(j):
        return "zeit"
    if ist_formlabel(j) or _FORM_ANFANG.search(j):
        return "form"
    return "thematisch"


# --- Benennung verständlich? ---------------------------------------------------------------------
_GENERISCH = {
    "theorie", "theorien", "methoden", "methodik", "methodologie", "grundlagen", "grundbegriffe", "praxis",
    "anwendung", "anwendungen", "technik", "techniken", "geschichte", "recht", "didaktik", "organisation",
    "verfahren", "probleme", "einzelfragen", "systematik", "entwicklung", "forschung", "terminologie", "struktur",
    "strukturen", "funktion", "funktionen", "wirkungen", "modelle", "begriff", "begriffe", "analyse", "analysen",
    "planung", "politik", "wirtschaft", "statistik", "verwaltung", "bau", "betrieb", "produktion", "aufbau",
    "eigenschaften", "messung", "messungen", "berechnung", "untersuchungen", "einzelne", "spezielle", "allgemeine",
    "sonstige", "arten", "typen", "formen", "aufgaben", "ziele", "kritik", "lehre", "bildung", "unterricht",
    "ausbildung", "fortbildung", "sprache", "stil", "inhalt", "überblick", "allgemeines", "beziehungen", "einfluss",
    "rolle", "bedeutung", "der", "die", "das", "des", "und", "in", "im", "mit", "von", "zur", "zum", "für", "als",
    "bei", "auf", "aus", "über", "u.", "ä.", "a.", "usw.", "einschl.", "teilgebiete", "teilbereiche", "aspekte",
}


def label_unklar(label: str) -> bool:
    """Ob die Benennung ohne den Pfad unverständlich ist: nur allgemeine Wörter („Theorie“, „Methoden und
    Verfahren“), Kleinschreibung am Anfang, sehr kurz oder ein bloßer Verweis."""
    s = label.strip()
    if len(s) < 4 or s[:1].islower() or s.startswith(("(", "s.", "s. ")):
        return True
    worte = [w for w in re.split(r"[\s,;/()\-]+", s.casefold()) if w]
    return all(w in _GENERISCH for w in worte)


def auswaehlen(klassen: list[dict], max_tiefe: int = MAX_TIEFE) -> tuple[list[dict], dict]:
    """Thematische Klassen der Tiefe 2 bis max_tiefe. Liefert (Auswahl, Statistik je Typ und Tiefe).
    Jede ausgewählte Klasse trägt Fachsystematik, Pfad, Sachschlagwörter (750 mit GND-ID) und Register."""
    geo = geo_begriffe(klassen)
    stat: dict = {"klassen": len(klassen), "typen": Counter(), "thematisch_je_tiefe": Counter()}
    fs = {k["notation"]: k["label"] for k in klassen if k["tiefe"] == 1}
    out = []
    for k in klassen:
        t = typ(k, geo)
        stat["typen"][t] += 1
        if t != "thematisch" or not 2 <= k["tiefe"] <= max_tiefe:
            continue
        stat["thematisch_je_tiefe"][k["tiefe"]] += 1
        gnd = [{"id": x["0"].removeprefix("(DE-588)"), "label": x["a"]} for x in k["register"]
               if x["tag"] == "750" and x.get("0", "").startswith("(DE-588)")]
        fsn = k["pfad"][0]["notation"]
        out.append({"notation": k["notation"], "label_rvk": k["label"], "tiefe": k["tiefe"],
                    "fachsystematik": {"notation": fsn, "label": fs.get(fsn, k["pfad"][0]["label"])},
                    "pfad": [p["label"] for p in k["pfad"]], "pfad_notationen": [p["notation"] for p in k["pfad"]],
                    "gnd": gnd, "register": sorted({x["a"] for x in k["register"] if x.get("a")
                                                    and x["tag"] in ("750", "710", "711")})[:10],
                    "uri": uri(k["notation"]), "url": url(k["notation"]), "label_unklar": label_unklar(k["label"])})
    stat["typen"] = dict(stat["typen"].most_common())
    stat["thematisch_je_tiefe"] = dict(sorted(stat["thematisch_je_tiefe"].items()))
    return out, stat
