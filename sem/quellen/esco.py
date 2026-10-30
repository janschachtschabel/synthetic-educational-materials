"""ESCO (European Skills, Competences, Qualifications and Occupations) über die öffentliche REST-API.

Geladen werden alle ESCO-Berufe mit deutschen Labels, Beschreibung, ISCO-Code und den Verknüpfungen
zu essenziellen und optionalen Kompetenzen, dazu die Details aller verknüpften Kompetenzen
(Beschreibung, Art, Wiederverwendbarkeit, ISCED-F-Feld). Die Rohdaten liegen kompakt im
Zwischenspeicher (data/cache/esco/) und gehören nicht ins Repo; ein erneuter Lauf lädt nur Fehlendes.
URIs bleiben die ESCO-Original-URIs (http://data.europa.eu/esco/...).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ..paths import DATA_DIR

API = "https://ec.europa.eu/esco/api"
QUELLE = {"name": "ESCO – Europäische Klassifikation für Fähigkeiten, Kompetenzen, Qualifikationen und Berufe",
          "uri": "https://esco.ec.europa.eu", "lizenz": "EU-Weiterverwendung (Beschluss 2011/833/EU)",
          "quellenangabe": "Quelle: ESCO, Europäische Kommission (https://esco.ec.europa.eu); deutsche Labels und "
                           "Beschreibungen unverändert übernommen"}
CACHE = DATA_DIR / "cache" / "esco"
BERUFE_PATH = CACHE / "berufe.jsonl"
KOMPETENZEN_PATH = CACHE / "kompetenzen.jsonl"
SPRACHE = "de"

# kurze Schlüssel für ESCO-Konzepte, die als Filter dienen
KOMPETENZART = {"http://data.europa.eu/esco/skill-type/knowledge": "wissen",
                "http://data.europa.eu/esco/skill-type/skill": "faehigkeit"}
REICHWEITE = {"http://data.europa.eu/esco/skill-reuse-level/occupation-specific": "berufsspezifisch",
              "http://data.europa.eu/esco/skill-reuse-level/sector-specific": "branchenspezifisch",
              "http://data.europa.eu/esco/skill-reuse-level/cross-sector": "branchenuebergreifend",
              "http://data.europa.eu/esco/skill-reuse-level/transversal": "querschnitt"}


def _get(path: str, versuche: int = 5, **params) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, headers={"User-Agent": "synthetic-educational-materials", "Accept": "application/json"})
    for i in range(versuche):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001 - Netz- und Serverfehler: mit Wartezeit wiederholen
            if i == versuche - 1:
                raise
            time.sleep(2 ** i)
    raise RuntimeError("unerreichbar")


def _text(obj: dict, feld: str) -> str:
    v = (obj.get(feld) or {}).get(SPRACHE)
    if isinstance(v, dict):
        v = v.get("literal")
    return (v or "").strip()


def beruf_uris() -> list[str]:
    """Alle Berufs-URIs (Suche ohne Suchbegriff, seitenweise; offset ist bei ESCO die Seitennummer)."""
    out, seite = [], 0
    while True:
        d = _get("/search", type="occupation", language=SPRACHE, limit=100, offset=seite, full="false")
        res = d.get("_embedded", {}).get("results", [])
        out += [r["uri"] for r in res]
        seite += 1
        if not res or len(out) >= d.get("total", 0):
            return list(dict.fromkeys(out))


def beruf_kompakt(o: dict) -> dict:
    links = o.get("_links", {})

    def rel(key):
        return [{"uri": s["uri"], "label": s.get("title", ""), "art": KOMPETENZART.get(s.get("skillType"), "andere")}
                for s in links.get(key) or []]

    code = o.get("code") or ""
    return {"uri": o["uri"], "label": (o.get("preferredLabel") or {}).get(SPRACHE) or o.get("title", ""),
            "alt_labels": list((o.get("alternativeLabel") or {}).get(SPRACHE) or [])[:10],
            "beschreibung": _text(o, "description"), "code": code, "isco": code.split(".")[0][:4],
            "essenziell": rel("hasEssentialSkill"), "optional": rel("hasOptionalSkill")}


def kompetenz_kompakt(o: dict) -> dict:
    links = o.get("_links", {})
    art = [KOMPETENZART.get(x["uri"], x["uri"].rsplit("/", 1)[-1]) for x in links.get("hasSkillType") or []]
    reuse = [REICHWEITE.get(x["uri"], x["uri"].rsplit("/", 1)[-1]) for x in links.get("hasReuseLevel") or []]
    isced = [{"uri": x["uri"], "label": x.get("title", "")} for x in links.get("broaderHierarchyConcept") or []
             if "/isced-f/" in x["uri"]]
    return {"uri": o["uri"], "label": (o.get("preferredLabel") or {}).get(SPRACHE) or o.get("title", ""),
            "alt_labels": list((o.get("alternativeLabel") or {}).get(SPRACHE) or [])[:10],
            "beschreibung": _text(o, "description"), "art": art[0] if art else "andere",
            "reichweite": reuse[0] if reuse else None, "isced_f": isced}


def _lade_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {r["uri"]: r for r in (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}


def _details(kind: str, uris: list[str], path: Path, kompakt, batch: int = 40, workers: int = 3,
             on_progress=None) -> dict[str, dict]:
    """Lädt Details zu den URIs (je Anfrage bis zu batch Konzepte) und hängt sie an path an."""
    have = _lade_cache(path)
    todo = [u for u in dict.fromkeys(uris) if u not in have]
    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    path.parent.mkdir(parents=True, exist_ok=True)

    fehlend: list[str] = []

    def hole(chunk):
        try:
            d = _get(f"/resource/{kind}", versuche=3 if len(chunk) > 1 else 5, uris=chunk, language=SPRACHE)
        except Exception:  # noqa: BLE001 - die API scheitert an einzelnen Konzepten: Batch teilen
            if len(chunk) == 1:
                fehlend.append(chunk[0])
                return []
            mitte = len(chunk) // 2
            return hole(chunk[:mitte]) + hole(chunk[mitte:])
        return [kompakt(v) for v in (d.get("_embedded") or {}).values()]

    with path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(workers) as pool:
        for i, rows in enumerate(pool.map(hole, chunks), 1):
            for r in rows:
                have[r["uri"]] = r
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            if on_progress:
                on_progress(i, len(chunks))
    if fehlend:
        (path.with_suffix(".fehlend.json")).write_text(json.dumps(fehlend, indent=1), encoding="utf-8")
    return have


def lade(on_progress=None) -> dict:
    """Alle Berufe und alle mit ihnen verknüpften Kompetenzen (aus dem Zwischenspeicher oder der API)."""
    berufe = _details("occupation", beruf_uris(), BERUFE_PATH, beruf_kompakt, on_progress=on_progress)
    skill_uris = [s["uri"] for b in berufe.values() for k in ("essenziell", "optional") for s in b[k]]
    kompetenzen = _details("skill", skill_uris, KOMPETENZEN_PATH, kompetenz_kompakt, on_progress=on_progress)
    meta = {**QUELLE, "api": API, "abgerufen": date.today().isoformat(), "berufe": len(berufe),
            "kompetenzen": len(kompetenzen)}
    (CACHE / "abruf.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"berufe": berufe, "kompetenzen": kompetenzen, "meta": meta}


def aus_cache() -> dict:
    meta_path = CACHE / "abruf.json"
    return {"berufe": _lade_cache(BERUFE_PATH), "kompetenzen": _lade_cache(KOMPETENZEN_PATH),
            "meta": json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else dict(QUELLE)}
