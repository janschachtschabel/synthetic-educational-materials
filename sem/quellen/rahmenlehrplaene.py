"""Lernfelder der KMK-Rahmenlehrpläne für die Berufsschule (duale Ausbildung).

Die KMK veröffentlicht die Rahmenlehrpläne nur als PDF. Eingelesen werden Markdown-Fassungen dieser PDFs
(z. B. mit Docling umgewandelt), Ordner per Umgebungsvariable RLP_MARKDOWN_DIR oder Argument. Übernommen
werden nur Lernfeld-Nummer, -Titel, Ausbildungsjahr und Zeitrichtwert, keine Ziel- oder Inhaltstexte.
Zwei Stellen im Plan werden gelesen und gegeneinander geprüft: die Übersichtstabelle in Teil V und die
Kopfzeilen der einzelnen Lernfelder („Lernfeld 3: …  2. Ausbildungsjahr  Zeitrichtwert: 80 Stunden“).
Die Downloadliste der KMK (Titel, Datum, Download-URL, Dateiname) liegt in data/cache/kmk/.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ..paths import DATA_DIR

LISTE_URL = "https://www.kmk.org/themen/berufliche-schulen/duale-berufsausbildung/downloadbereich-rahmenlehrplaene.html"
QUELLE = {"name": "KMK – Rahmenlehrpläne für den berufsbezogenen Unterricht in der Berufsschule",
          "uri": LISTE_URL, "lizenz": "keine offene Lizenz angegeben; übernommen nur Nummer, Titel, Jahr, Zeitrichtwert",
          "quellenangabe": "Quelle: Sekretariat der Kultusministerkonferenz, Rahmenlehrpläne (Downloadbereich, "
                           "https://www.kmk.org); Lernfeldtitel unverändert, Ziel- und Inhaltstexte nicht übernommen"}
CACHE = DATA_DIR / "cache" / "kmk"
LISTE_PATH = CACHE / "rahmenlehrplaene_liste.json"

# „Lernfeld 9 EG: Titel“, „Lernfeld 10a: Titel“, „Lernfeld 3 Titel“ (ohne Doppelpunkt, dann ohne Kürzel)
_LF_KOPF = re.compile(r"^(?:#+\s*)?Lernfeld\s+(\d{1,2})(?:\s?\(?([A-Z]{1,4}|[a-z])\)?)?\s*[:.]\s*(.*)$"
                      r"|^(?:#+\s*)?Lernfeld\s+(\d{1,2})\s+(?!\d)(.*)$", re.M)
_JAHR = re.compile(r"(\d)\.\s*(?:Ausbildungsjahr|Jahr)")
_ZEIT = re.compile(r"Zeitrichtwert(?:e)?\s*:?\s*(\d{2,3})\.?\s*(?:Stunden|Std)", re.I)
_UEBERSICHT = re.compile(r"Übersicht\s+(?:über\s+)?die\s+Lernfelder|Lernfeldübersicht", re.I)


def downloadliste(neu: bool = False) -> list[dict]:
    """Titel, Datum, Download-URL und Dateiname aller Rahmenlehrpläne im KMK-Downloadbereich."""
    if LISTE_PATH.exists() and not neu:
        return json.loads(LISTE_PATH.read_text(encoding="utf-8"))
    req = urllib.request.Request(LISTE_URL, headers={"User-Agent": "synthetic-educational-materials"})
    with urllib.request.urlopen(req, timeout=60) as r:
        src = r.read().decode("utf-8")
    rows, seen = [], set()
    for titel, href, did, datum in re.findall(r'class="filelink" title="PDF Download: ([^"]*)" '
                                              r'href="([^"]*download%5D=(\d+)[^"]*)".*?<div class="date">\s*([\d.]+)',
                                              src, re.S):
        if did not in seen:
            seen.add(did)
            rows.append({"titel": html.unescape(titel).strip(), "id": did, "datum": datum,
                         "url": "https://www.kmk.org" + html.unescape(href)})

    def datei(r):  # Dateiname steht nur im Content-Disposition-Kopf
        req = urllib.request.Request(r["url"], method="HEAD", headers={"User-Agent": "synthetic-educational-materials"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', resp.headers.get("Content-Disposition", ""))
        return {**r, "datei": urllib.parse.unquote(m.group(1)) if m else None}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(datei, rows))
    CACHE.mkdir(parents=True, exist_ok=True)
    LISTE_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def _zellen(zeile: str) -> list[str]:
    return [re.sub(r"\s+", " ", c).strip() for c in zeile.strip().strip("|").split("|")]


# Ergänzungsstrich vor Konjunktionen bleibt stehen („Roh- und Hilfsstoffe“), sonst Trennstrich auflösen
_ERGAENZUNG = r"(?!(?:und|oder|bis|sowie|bzw)\b)"


def _titel(t: str) -> str:
    t = re.sub(r"(\w)- " + _ERGAENZUNG + r"(\w)",
               lambda m: m.group(1) + ("-" if m.group(2).isupper() else "") + m.group(2), t)
    return re.sub(r"\s+", " ", t).strip(" *")


def _nr(c: str) -> str | None:
    m = re.fullmatch(r"(?:LF\s*)?(\d{1,2})\s*(?:\(?([A-Za-z]{1,4})\)?)?\.?", c.strip())
    return m.group(1) + (m.group(2) or "").lower() if m else None


def _kopf(z: str) -> tuple[str, str] | None:
    """Nummer (mit Kürzel der Fachrichtung, klein) und Resttext einer Lernfeld-Kopfzeile."""
    m = _LF_KOPF.match(z.strip())
    if not m:
        return None
    if m.group(1):
        return m.group(1) + (m.group(2) or "").lower(), m.group(3)
    return m.group(4), m.group(5)


def _tabellenzeilen(md: str):
    """Tabellenzeilen nach jeder Lernfeldübersicht bis zur ersten Lernfeldbeschreibung danach."""
    for m in _UEBERSICHT.finditer(md):
        for zeile in md[m.end():m.end() + 30000].splitlines():
            if zeile.lstrip().startswith("|"):
                yield _zellen(zeile)
            elif _kopf(zeile) and _ZEIT.search(md[m.end():m.end() + 30000]):
                break


def tabelle(md: str) -> list[dict]:
    """Lernfelder aus den Übersichtstabellen (Teil V): Nummer, Titel, Zeitrichtwerte je Jahr."""
    out, kopf, gesehen = [], [], set()
    for z in _tabellenzeilen(md):
        if all(set(c) <= set("-: ") for c in z):
            continue
        if any(_JAHR.search(c) for c in z) and not _nr(z[0]):
            kopf = z  # Zeile mit „1. Jahr | 2. Jahr …“
            continue
        nr = _nr(z[0]) if z else None
        if not nr and len(z) > 1 and not z[0]:
            # Docling zieht zweistellige Nummern gelegentlich in die Titelzelle: „| | 10 Titel |“
            m = re.match(r"(\d{1,2}(?:\s?[A-Za-z](?![a-zäöüß])|\s+\(?[A-Z]{1,4}\)?)?)\s+(\D.*)$", z[1])
            if m:
                nr, z = _nr(m.group(1)), [m.group(1), m.group(2), *z[2:]]
        if not nr or nr in gesehen:
            continue
        titel = next((c for c in z[1:] if c and not re.fullmatch(r"[\d\s,./–-]*", c)), "")
        stunden = {}
        for i, c in enumerate(z):
            if i == 0 or not re.fullmatch(r"\d{2,3}", c):
                continue
            j = _JAHR.search(kopf[i]) if i < len(kopf) else None
            stunden[int(j.group(1)) if j else 0] = int(c)
        gesehen.add(nr)
        out.append({"nr": nr, "titel": _titel(titel), "stunden": stunden})
    return out


def kopfzeilen(md: str) -> list[dict]:
    """Lernfelder aus den Kopfzeilen der einzelnen Lernfeldbeschreibungen."""
    out, gesehen = [], set()
    zeilen = md.splitlines()
    for i, z in enumerate(zeilen):
        k = _kopf(z)
        if not k:
            continue
        nr, titel = k
        if nr in gesehen:
            continue
        umfeld = " ".join(zeilen[i:i + 8])
        zeit = _ZEIT.search(umfeld)
        if not zeit:
            continue  # Verweis im Fließtext, keine Kopfzeile
        titel = titel.strip()
        if not titel or _JAHR.search(titel) or _ZEIT.search(titel):
            titel = next((t.strip("# ") for t in zeilen[i + 1:i + 4] if t.strip() and not _JAHR.search(t)
                          and not _ZEIT.search(t) and not t.startswith("[")), "")
        titel = _ZEIT.split(_JAHR.split(titel)[0])[0]
        jahr = _JAHR.search(umfeld)
        gesehen.add(nr)
        out.append({"nr": nr, "titel": _titel(titel), "jahr": int(jahr.group(1)) if jahr else None,
                    "stunden": int(zeit.group(1))})
    return out


def _schluessel(nr: str) -> tuple[int, str]:
    return int(re.match(r"\d+", nr).group()), nr


def lernfelder(md: str) -> dict:
    """Beide Lesarten zusammengeführt, mit Befund zur Vollständigkeit."""
    tab, kopf = tabelle(md), kopfzeilen(md)
    nach_nr = {k["nr"]: k for k in kopf}
    felder = []
    for nr in sorted({*(t["nr"] for t in tab), *nach_nr}, key=_schluessel):
        t = next((x for x in tab if x["nr"] == nr), None)
        k = nach_nr.get(nr)
        jahr = (k or {}).get("jahr") or (min(y for y in t["stunden"] if y) if t and any(t["stunden"]) else None)
        stunden = (k or {}).get("stunden") or (sum(t["stunden"].values()) if t and t["stunden"] else None)
        felder.append({"nr": nr, "titel": (t or {}).get("titel") or (k or {}).get("titel", ""),
                       "jahr": jahr, "stunden": stunden,
                       "in_tabelle": t is not None, "in_kopfzeilen": k is not None})
    zahlen = sorted({_schluessel(f["nr"])[0] for f in felder})
    befund = {"tabelle": len(tab), "kopfzeilen": len(kopf), "lernfelder": len(felder),
              "luecken": [n for n in range(1, max(zahlen) + 1) if n not in zahlen] if zahlen else [],
              "ohne_titel": [f["nr"] for f in felder if not f["titel"]],
              "ohne_stunden": [f["nr"] for f in felder if not f["stunden"]],
              "ohne_jahr": [f["nr"] for f in felder if not f["jahr"]],
              "nur_tabelle": [f["nr"] for f in felder if not f["in_kopfzeilen"]],
              "nur_kopfzeilen": [f["nr"] for f in felder if not f["in_tabelle"]]}
    if not felder:
        befund["status"] = "keine_lernfelder"
    elif not tab:
        befund["status"] = "ohne_tabelle"
    elif befund["luecken"] or befund["ohne_titel"] or befund["ohne_stunden"] or befund["ohne_jahr"]:
        befund["status"] = "unvollstaendig"
    elif befund["nur_tabelle"] or befund["nur_kopfzeilen"]:
        befund["status"] = "abweichend"
    else:
        befund["status"] = "vollstaendig"
    return {"lernfelder": felder, "befund": befund}
