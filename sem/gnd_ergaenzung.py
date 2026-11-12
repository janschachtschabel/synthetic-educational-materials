"""Ergänzungen zu GND-Themen aller Bereiche: Ersatz für Motiv-Sätze und Kurzbeschreibungen.

Zwei Probleme der gebauten Themen (Prüfung 2026-11-12):
- Rund 280 Themen verweisen auf GND-Sätze für Bildmotive („Amsterdam <Motiv>“, „Mais <Motiv>“) statt auf das
  Sachschlagwort bzw. Geografikum. Das verfälscht die ID und lenkt Zuordnung und Text („Der Drache als Motiv“).
- Die meisten GND-Themen haben keine Beschreibung; im Volltext-Prompt fehlte dann „Worum es fachlich geht“.

`python -m sem gnd-ergaenzen` (braucht lobid und Wikidata, also Jans Rechner) schreibt
data/mappings/gnd_ergaenzungen.jsonl: je GND-URI der Ersatz für einen Motiv-Satz und eine Kurzbeschreibung (GND-
Definition, sonst Wikidata-Beschreibung, beide CC0). Die Bereiche wenden die Datei beim Laden ihrer Themen an
(anwenden), die Themen-Dateien selbst bleiben unverändert; so übersteht die Ergänzung einen Neuaufbau der Mappings.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache

from .paths import MAPPING_DIR

DATEI = MAPPING_DIR / "gnd_ergaenzungen.jsonl"
GND_PREFIX = "https://d-nb.info/gnd/"
# Feld der Themenbeschreibung je Bereich
BESCHREIBUNGSFELD = {"schule": "definition", "elementar": "definition", "erwachsenenbildung": "definition",
                     "hochschule": "beschreibung_de"}


def _gnd_von(topic: dict) -> tuple[str | None, str]:
    """(GND-URI, GND-Label) eines Themas: die ID selbst oder der verknüpfte GND-Treffer (Schule-Themenbaum)."""
    if topic["id"].startswith(GND_PREFIX):
        return topic["id"], topic.get("label_gnd") or topic.get("label_de", "")
    g = topic.get("gnd")
    if isinstance(g, dict) and str(g.get("uri", "")).startswith(GND_PREFIX):
        return g["uri"], g.get("label", "")
    return None, ""


@lru_cache(maxsize=1)
def laden() -> dict[str, dict]:
    if not DATEI.exists():
        return {}
    zeilen = [json.loads(z) for z in DATEI.read_text(encoding="utf-8").splitlines() if z.strip()]
    return {z["uri"]: z for z in zeilen}


def anwenden(topics: list[dict], bereich: str, ergaenzungen: dict[str, dict] | None = None) -> list[dict]:
    """Themen mit ersetzten Motiv-Sätzen und ergänzten Beschreibungen (neue Objekte, Eingabe bleibt unverändert)."""
    erg = laden() if ergaenzungen is None else ergaenzungen
    if not erg:
        return topics
    feld = BESCHREIBUNGSFELD.get(bereich, "definition")
    out = []
    for t in topics:
        uri, _ = _gnd_von(t)
        e = erg.get(uri) if uri else None
        if not e:
            out.append(t)
            continue
        t = dict(t)
        if (ersatz := e.get("motiv_ersatz")):
            t["gnd_motiv"] = uri
            if t["id"] == uri:
                t["id"] = ersatz["uri"]
                if "label_gnd" in t:
                    t["label_gnd"] = ersatz["label"]
            else:
                t["gnd"] = {"uri": ersatz["uri"], "label": ersatz["label"]}
        if not (t.get(feld) or "").strip() and e.get("beschreibung"):
            t[feld] = e["beschreibung"]
            t["beschreibung_quelle"] = e["beschreibung_quelle"]
        out.append(t)
    return out


def sammeln() -> dict[str, dict]:
    """GND-URIs aller Bereiche mit Label, Motiv-Kennzeichen und ob eine Beschreibung fehlt."""
    from .bereiche import get
    from .quellen.gnd import ist_motiv

    out: dict[str, dict] = {}
    for key, feld in BESCHREIBUNGSFELD.items():
        try:
            topics = get(key)._topics_roh()
        except FileNotFoundError:
            continue
        for t in topics:
            uri, label = _gnd_von(t)
            if not uri:
                continue
            o = out.setdefault(uri, {"uri": uri, "label": label, "motiv": ist_motiv(label), "ohne_beschreibung": False})
            o["ohne_beschreibung"] |= not (t.get(feld) or "").strip()
    return out


def bauen(limit: int | None = None, on_progress=None) -> dict:
    """Ergänzungen über lobid (Motiv-Ersatz, GND-Definition) und Wikidata (Beschreibung) holen und speichern."""
    from .quellen import gnd, wikidata

    kandidaten = [k for k in sammeln().values() if k["motiv"] or k["ohne_beschreibung"]]
    if limit:
        kandidaten = kandidaten[:limit]
    zeilen: dict[str, dict] = {}
    for i, k in enumerate(kandidaten, 1):
        z = {"uri": k["uri"], "label": k["label"]}
        if k["motiv"] and (ersatz := gnd.motiv_ersatz(k["label"])):
            z["motiv_ersatz"] = {"uri": ersatz["uri"], "label": ersatz["label"], "typen": ersatz["typen"]}
        zeilen[k["uri"]] = z
        if on_progress and i % 200 == 0:
            on_progress(f"Motiv-Ersatz {i}/{len(kandidaten)}")
    # GND-Definition: für die Themen-URI bzw. den Ersatz
    ziel = {u: (z.get("motiv_ersatz") or {}).get("uri", u) for u, z in zeilen.items()}
    saetze = gnd.hole(sorted(set(ziel.values())))
    for u, z in zeilen.items():
        d = (saetze.get(ziel[u]) or {}).get("definition", "").strip()
        if d:
            z.update(beschreibung=d, beschreibung_quelle={"name": "Gemeinsame Normdatei (GND), Definition",
                                                         "uri": ziel[u], "lizenz": "CC0-1.0"})
    offen = {ziel[u].rsplit("/", 1)[-1]: u for u, z in zeilen.items() if not z.get("beschreibung")}
    for gid, w in wikidata.beschreibungen(list(offen)).items():
        z = zeilen[offen[gid]]
        z.update(beschreibung=w["beschreibung"],
                 beschreibung_quelle={"name": "Wikidata, Beschreibung", "uri": f"https://www.wikidata.org/wiki/"
                                      f"{w['wikidata']}", "lizenz": "CC0-1.0"})
    behalten = [z for z in zeilen.values() if z.get("motiv_ersatz") or z.get("beschreibung")]
    for z in behalten:
        z["datum"] = date.today().isoformat()
    DATEI.write_text("".join(json.dumps(z, ensure_ascii=False) + "\n" for z in sorted(behalten, key=lambda z: z["uri"])),
                     encoding="utf-8")
    laden.cache_clear()
    return {"kandidaten": len(kandidaten), "motiv": sum(1 for k in kandidaten if k["motiv"]),
            "motiv_ersetzt": sum(1 for z in behalten if z.get("motiv_ersatz")),
            "beschreibungen": sum(1 for z in behalten if z.get("beschreibung")),
            "davon_wikidata": sum(1 for z in behalten if "Wikidata" in (z.get("beschreibung_quelle") or {}).get("name", ""))}
