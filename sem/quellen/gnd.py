"""GND und Bibliothekskatalog über lobid (hbz), beide CC0.

- lobid-gnd: Sachschlagwörter mit Typ, Synonymen, Oberbegriffen und GND-Sachgruppe.
- lobid-resources: hbz-Verbundkatalog. Lehrbücher erkennt man an natureOfContent „Lehrbuch“,
  Hochschulschriften an type:Thesis. Aggregationen liefern höchstens 100 Buckets; deshalb wird
  nach Notations-Präfixen (z. B. DDC 530) aufgeteilt.
- GND-Sachgruppen (DNB, CC0) als Vokabular.

Alle Antworten werden in data/cache/lobid/ zwischengespeichert, ein erneuter Lauf fragt nichts doppelt.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from ..paths import DATA_DIR, VOCAB_DIR
from . import wiederholen

GND_SEARCH = "https://lobid.org/gnd/search"
RES_SEARCH = "https://lobid.org/resources/search"
SACHGRUPPEN_URL = "https://d-nb.info/standards/vocab/gnd/gnd-sc.ttl"
LEHRBUCH = "https://d-nb.info/gnd/4123623-3"
CACHE_DIR = DATA_DIR / "cache" / "lobid"

# Typen, die als Thema taugen. Personen, Körperschaften, Orte, Werke, Produkte, Software und
# Personengruppen haben in der GND eigene Typen und fallen damit heraus; Nomenklaturen (einzelne
# Stoffe, Arten) sind als Lehrthemen zu speziell.
THEMEN_TYPEN = {"SubjectHeadingSensoStricto", "HistoricSingleEventOrEra"}

_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>", "", s or "")).strip().casefold()


def _get(base: str, params: dict, pause: float = 0.05, versuche: int = 5) -> dict:
    url = base + "?" + urllib.parse.urlencode(params)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = CACHE_DIR / key[:2] / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for i in range(versuche):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "synthetic-educational-materials"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except Exception as e:  # noqa: BLE001 - Netz- und Serverfehler wiederholen, 4xx außer 429 nicht
            if i == versuche - 1 or not wiederholen(e):
                raise
            time.sleep(2 ** i)
    time.sleep(pause)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# --- GND-Sachschlagwörter ------------------------------------------------------------------

def kompakt(m: dict) -> dict:
    typen = [t for t in m.get("type", []) if t not in ("AuthorityResource", "SubjectHeading")]
    return {
        "uri": m["id"], "gnd_id": m.get("gndIdentifier"), "label": m.get("preferredName"), "typen": typen,
        "synonyme": (m.get("variantName") or [])[:10],
        "sachgruppen": [{"uri": s["id"], "label": s.get("label")} for s in m.get("gndSubjectCategory", [])],
        "oberbegriffe": [{"uri": b["id"], "label": b.get("label")} for b in m.get("broaderTermGeneral", [])],
        "definition": (m.get("definition") or [""])[0],
    }


def ist_thema(rec: dict) -> bool:
    return bool(THEMEN_TYPEN & set(rec["typen"])) and "NomenclatureInBiologyOrChemistry" not in rec["typen"]


def suche_exakt(label: str) -> dict | None:
    """Sachschlagwort, dessen Vorzugs- oder Synonymname exakt label ist (ohne Groß/Klein)."""
    norm = _norm(label)
    if not norm or len(norm) < 3:
        return None
    q = label.replace('"', "")
    data = _get(GND_SEARCH, {"q": f'preferredName:"{q}" OR variantName:"{q}"', "filter": "type:SubjectHeading",
                             "format": "json", "size": 20})
    treffer = [kompakt(m) for m in data.get("member", [])
               if any(_norm(n) == norm for n in [m.get("preferredName", "")] + (m.get("variantName") or []))]
    treffer.sort(key=lambda r: (not ist_thema(r), _norm(r["label"]) != norm))
    return treffer[0] if treffer else None


def hole(uris: list[str], batch: int = 50) -> dict[str, dict]:
    """GND-Datensätze zu URIs (in Paketen)."""
    out = {}
    ids = [u.rsplit("/", 1)[-1] for u in uris]
    for i in range(0, len(ids), batch):
        part = ids[i:i + batch]
        q = "gndIdentifier:(" + " OR ".join(f'"{x}"' for x in part) + ")"
        data = _get(GND_SEARCH, {"q": q, "format": "json", "size": len(part)})
        for m in data.get("member", []):
            out[m["id"]] = kompakt(m)
    return out


# --- Katalog -------------------------------------------------------------------------------

def _buckets(data: dict, feld: str) -> list[tuple[str, int]]:
    agg = (data.get("aggregation") or data.get("aggregations") or {}).get(feld) or []
    out = []
    for b in agg:
        key = b.get("key") or b.get("id")
        if key:
            out.append((key, int(b.get("doc_count", 0))))
    return out


def katalog_top(praefix: str, art: str) -> list[tuple[str, int]]:
    """Häufigste Schlagwörter (GND-URIs) in Lehrbüchern bzw. Hochschulschriften unter einem
    Notations-Präfix. art: "lehrbuch" oder "hochschulschrift"."""
    filt = f'natureOfContent.id:"{LEHRBUCH}"' if art == "lehrbuch" else "type:Thesis"
    data = _get(RES_SEARCH, {"q": f"{filt} AND subject.notation:{praefix}*", "format": "json", "size": 0,
                             "aggregations": "subject.componentList.id"})
    return [(k, n) for k, n in _buckets(data, "subject.componentList.id") if k.startswith("https://d-nb.info/gnd/")]


def katalog_zaehlen(uri: str) -> dict:
    """Titel, Lehrbücher und Hochschulschriften mit diesem Schlagwort."""
    q = f'subject.componentList.id:"{uri}"'
    alle = _get(RES_SEARCH, {"q": q, "format": "json", "size": 0, "aggregations": "type"})
    lehr = _get(RES_SEARCH, {"q": f'{q} AND natureOfContent.id:"{LEHRBUCH}"', "format": "json", "size": 0})
    thesis = dict(_buckets(alle, "type")).get("Thesis", 0)
    return {"titel": int(alle.get("totalItems", 0)), "lehrbuecher": int(lehr.get("totalItems", 0)),
            "hochschulschriften": thesis}


# --- Sachgruppen-Vokabular -------------------------------------------------------------------

def _eltern(n: str) -> list[str]:
    """Mögliche übergeordnete Notationen, nächste zuerst: 31.1b → 31.1, 31; 28p → 28."""
    out = []
    while True:
        if re.search(r"[a-z]$", n):
            n = n[:-1]
        elif "." in n:
            n = n.rsplit(".", 1)[0]
        else:
            return out
        out.append(n)


def lade_sachgruppen(ziel: Path = VOCAB_DIR / "gnd_sachgruppen.json") -> int:
    """GND-Sachgruppen als Repo-Vokabular. Die SKOS-Datei hat kaum broader-Angaben; die Hierarchie
    wird aus der Notation abgeleitet (31.1b → 31.1 → 31). Sachgruppen mit p (Personen) und
    m (Produkte) sind markiert."""
    import tempfile

    import rdflib
    from rdflib.namespace import SKOS

    with tempfile.TemporaryDirectory() as tmp:
        ttl = Path(tmp) / "gnd-sc.ttl"
        urllib.request.urlretrieve(SACHGRUPPEN_URL, ttl)
        g = rdflib.Graph()
        g.parse(ttl)
    concepts = {}
    for s in g.subjects(rdflib.RDF.type, SKOS.Concept):
        labels = {o.language: str(o) for o in g.objects(s, SKOS.prefLabel)}
        n = str(g.value(s, SKOS.notation) or str(s).rsplit("#", 1)[-1])
        concepts[n] = {"uri": str(s), "notation": n, "label": labels.get("de") or next(iter(labels.values()), n),
                       "broader": None, "level": 0, "art": "personen" if n.endswith("p") else
                       "produkte" if n.endswith("m") else "sache"}
    for n, c in concepts.items():
        for eltern in _eltern(n):
            if eltern in concepts:
                c["broader"] = concepts[eltern]["uri"]
                break
    by_uri = {c["uri"]: c for c in concepts.values()}
    for c in concepts.values():
        lvl, b = 0, c["broader"]
        while b:
            lvl, b = lvl + 1, by_uri[b]["broader"]
        c["level"] = lvl
    data = {"scheme": {"name": "gnd_sachgruppen", "uri": "https://d-nb.info/standards/vocab/gnd/gnd-sc",
                       "title": "GND-Sachgruppen", "source": SACHGRUPPEN_URL,
                       "public_url": "https://d-nb.info/standards/vocab/gnd/gnd-sc.html", "license": "CC0-1.0",
                       "retrieved": date.today().isoformat()},
            "concepts": sorted(concepts.values(), key=lambda c: c["notation"])}
    ziel.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(concepts)
