"""Lesbare Übersicht über Seeds und Materialien (für die Vorschau in der Oberfläche)."""

from __future__ import annotations

import json
from pathlib import Path

from . import schema as S


def lade(path: Path, limit: int | None = None) -> list[dict]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
                if limit and len(out) >= limit:
                    break
    return out


def dateiart(path: Path) -> str:
    """„materialien“, wenn der erste Datensatz einen Volltext hat, sonst „seeds“."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return "materialien" if ist_material(json.loads(line)) else "seeds"
    except (OSError, json.JSONDecodeError):
        pass
    return "seeds"


def ist_material(rec: dict) -> bool:
    return S.F_FULLTEXT in rec


def _labels(rec: dict, feld: str) -> str:
    return ", ".join(rec.get(feld + "_DISPLAYNAME") or rec.get(feld) or [])


def metadaten(rec: dict) -> dict[str, str]:
    """Feste Metadaten des Seeds als Label → Wert (leere Felder weggelassen)."""
    t = rec.get(S.F_THEMA, {})
    p = rec.get(S.F_PROVENANCE, {}).get(S.F_THEMA, {})
    zeilen = {
        "Bildungsstufe": _labels(rec, S.F_CONTEXT),
        "Studienfach": _labels(rec, S.F_TAXON_UNI),
        "Schulfach": _labels(rec, S.F_TAXON),
        "Inhaltstyp": _labels(rec, S.F_LRT),
        "Zielgruppe": _labels(rec, S.F_ROLE),
        "Sprache": ", ".join(rec.get(S.F_LANGUAGE, [])),
        "Thema": t.get("label_de") or t.get("label_en", ""),
        "Themen-URI": t.get("id", ""),
        "Themenquelle": p.get("quelle", ""),
        "Lehrebene": t.get("lehrebene") or "",
        "Niveau": (rec.get(S.F_NIVEAU) or {}).get("label", ""),
        "Textsorte": rec.get("synth:textsorte", ""),
        "Didaktische Funktion": rec.get(S.F_DIDAKTIK, ""),
        "Bearbeitungsdauer": f"{rec[S.F_LEARNING_TIME]} Minuten" if rec.get(S.F_LEARNING_TIME) else "",
        "Status": ", ".join(f"{k}: {v}" for k, v in p.get("status", {}).items()),
        "Belege": ", ".join(p.get("belege", {}).get("erfuellt", [])),
    }
    return {k: str(v) for k, v in zeilen.items() if v}


def suchtext(rec: dict) -> str:
    t = rec.get(S.F_THEMA, {})
    teile = [rec.get("id", ""), t.get("label_de", ""), t.get("label_en", ""), _labels(rec, S.F_TAXON_UNI),
             _labels(rec, S.F_LRT), rec.get(S.F_TITLE, ""), " ".join(rec.get(S.F_KEYWORD, []))]
    return " ".join(teile).casefold()
