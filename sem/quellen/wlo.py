"""Keyword-Fach-Kombinationen aus einem WirLernenOnline-Export (edu-sharing, CSV mit ';').

Genutzt werden ausschließlich die Felder Node-ID, Bildungsstufe, Fach und Keywords. Titel,
Beschreibungen, URLs, Autor:innen und alle übrigen Felder werden nicht gelesen. Die Keywords
dienen nur als Belege: übernommen wird später allein, was exakt einem GND-Sachschlagwort
entspricht. Der Export selbst gehört nicht ins Repo; das Ergebnis dieses Moduls liegt im
Zwischenspeicher (data/cache).
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..paths import DATA_DIR

QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
          "hinweis": "nur Keywords in Verbindung mit Hochschulfächern genutzt; Beleg als Zahl der Datensätze "
                     "und, wo der Export sie enthält, Node-IDs"}
NODE_URI = "https://redaktion.openeduhub.net/edu-sharing/components/render/{}"
FELDER = {"node": "sys:node-uuid", "kontext": "ccm:educationalcontext_DISPLAYNAME", "fach": "ccm:taxonid",
          "fach_uni": "ccm:oeh_taxonid_university", "keywords": "cclom:general_keyword",
          "curriculum": "ccm:curriculum", "berufsgruppe": "ccm:oeh_profession_group"}
# ccm:taxonid mischt Schul- und Hochschulfächer; Hochschulfächer erkennt man an der URI
HOCHSCHULFACH = "hochschulfaechersystematik"
CACHE = DATA_DIR / "cache"


PFLICHT = {"fach", "keywords"}


def _spalte(fieldnames: list[str], key: str) -> str | None:
    name = FELDER[key]
    for f in fieldnames:
        if f == name or f.endswith("." + name):
            return f
    if key in PFLICHT:
        raise KeyError(f"Spalte {name} fehlt im Export")
    return None  # z. B. Export ohne Node-ID oder Bildungsstufe


def _liste(row: dict, col: str | None) -> list[str]:
    return [x.strip() for x in (row.get(col) or "").split(",") if x.strip()] if col else []


def datensaetze(paths: list, felder: list[str], pflicht: tuple[str, ...] = (), delimiter: str = ";"):
    """Je Datensatz der Exporte (node, {feld: [werte]}) für Schlüssel aus FELDER; gelesen werden nur diese Spalten.
    node ist die Node-ID oder, ohne die Spalte, ein interner Zeilenschlüssel „#datei:zeile“ (wird nicht ausgegeben).
    Fehlt eine Spalte aus pflicht, KeyError; andere fehlende Spalten ergeben leere Listen."""
    csv.field_size_limit(10 ** 9)
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=delimiter)
            col = {k: next((c for c in r.fieldnames if c == FELDER[k] or c.endswith("." + FELDER[k])), None)
                   for k in ("node", *felder)}
            fehlt = [FELDER[k] for k in pflicht if not col[k]]
            if fehlt:
                raise KeyError(f"Spalten fehlen im Export: {', '.join(fehlt)}")
            for i, row in enumerate(r):
                node = row[col["node"]].strip() if col["node"] else f"#{Path(path).name}:{i}"
                yield node, {k: _liste(row, col[k]) for k in felder}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def lese_keywords(path: Path, kontext: str | None = "Hochschule", nur_hochschulfaecher: bool = True,
                  delimiter: str = ";") -> dict:
    """keyword (normalisiert) -> {"schreibweisen": Counter, "nodes": [...], "faecher": Counter(uri)}

    nur_hochschulfaecher: Fächer nur aus Hochschulfach-URIs (taxonid gefiltert, taxonid_university);
    Nodes ohne Hochschulfach werden übersprungen.
    """
    csv.field_size_limit(10 ** 9)
    out: dict = defaultdict(lambda: {"schreibweisen": Counter(), "nodes": [], "faecher": Counter()})
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f, delimiter=delimiter)
        col = {k: _spalte(r.fieldnames, k) for k in FELDER}
        for i, row in enumerate(r):
            if kontext and col["kontext"] and kontext not in _liste(row, col["kontext"]):
                continue
            uris = _liste(row, col["fach"])
            if nur_hochschulfaecher:
                uris = list(dict.fromkeys([u for u in uris if HOCHSCHULFACH in u] + _liste(row, col["fach_uni"])))
                if not uris:
                    continue
            # ohne Node-ID nur zählen (Zeilennummer als interner Schlüssel, wird nicht ausgegeben)
            node = row[col["node"]].strip() if col["node"] else f"#{Path(path).name}:{i}"
            for kw in {k.strip() for k in row[col["keywords"]].split(",") if k.strip()}:
                e = out[_norm(kw)]
                e["schreibweisen"][kw] += 1
                e["nodes"].append(node)
                e["faecher"].update(uris)
    return dict(out)


def extrahiere(paths: list[Path], kontext: str | None = "Hochschule", min_nodes: int = 3,
               out_path: Path | None = None) -> Path:
    """Schreibt die Keywords, die in mindestens min_nodes Nodes vorkommen, in den Zwischenspeicher."""
    data: dict = {}
    for p in paths:
        for k, v in lese_keywords(p, kontext).items():
            e = data.setdefault(k, {"schreibweisen": Counter(), "nodes": [], "faecher": Counter()})
            e["schreibweisen"].update(v["schreibweisen"])
            e["nodes"] += v["nodes"]
            e["faecher"].update(v["faecher"])
    rows = {}
    for k, v in data.items():
        nodes = set(v["nodes"])
        if len(nodes) >= min_nodes:
            rows[k] = {"schreibweisen": dict(v["schreibweisen"].most_common(5)), "anzahl": len(nodes),
                       "nodes": sorted(n for n in nodes if not n.startswith("#")), "faecher": dict(v["faecher"])}
    out_path = out_path or CACHE / f"wlo_keywords_{_norm(kontext or 'alle').replace(' ', '_')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"quelle": QUELLE, "kontext": kontext, "min_nodes": min_nodes, "keywords": rows},
                                   ensure_ascii=False), encoding="utf-8")
    return out_path
