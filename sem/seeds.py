"""Seeds für einen oder mehrere Bildungsbereiche erzeugen und mit Statistik speichern."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from . import schema as S
from .bereiche import get as get_bereich


def build(counts: dict[str, int], balance: dict[str, str] | None = None, seed: int = 42,
          auswahl: dict[str, dict[str, list[str]]] | None = None) -> list[dict]:
    """counts: Bildungsbereich → Anzahl Seeds. balance: Bildungsbereich → Gleichverteilungsfeld.
    auswahl: Bildungsbereich → Filter (siehe Bildungsbereich.auswahl_optionen), fehlende = Standard."""
    from .bereiche.base import build_seeds

    out = []
    for key, n in counts.items():
        if n > 0:
            b = get_bereich(key)
            out += build_seeds(b, n, (balance or {}).get(key), seed, auswahl=(auswahl or {}).get(key))
    return out


def _tp(seed: dict) -> dict:
    """Herkunftsangabe des Themas."""
    return seed.get(S.F_PROVENANCE, {}).get(S.F_THEMA, {})


def stats(seeds: list[dict]) -> dict:
    def count(field):
        return Counter(v for s in seeds for v in s.get(field + "_DISPLAYNAME", [])[:1])

    themen = Counter(s[S.F_THEMA]["id"] for s in seeds)
    per_group = Counter(s["synth:seed_parameter"]["gruppe"] for s in seeds)
    status_keys = list(dict.fromkeys(k for s in seeds for k in _tp(s).get("status", {})))
    return {
        "anzahl": len(seeds),
        "bildungsbereiche": dict(Counter(s[S.F_BEREICH] for s in seeds)),
        "themen_abgedeckt": len(themen),
        "seeds_je_gruppe": {"gruppen": len(per_group), "min": min(per_group.values(), default=0),
                            "max": max(per_group.values(), default=0)},
        "seeds_je_thema": {"min": min(themen.values(), default=0), "max": max(themen.values(), default=0)},
        "inhaltstyp": dict(count(S.F_LRT).most_common()),
        "fach_primaer": dict((count(S.F_TAXON_UNI) + count(S.F_PROFESSION)).most_common()),
        "niveau": dict(Counter(s[S.F_NIVEAU]["label"] for s in seeds if S.F_NIVEAU in s)),
        "lehrebene": dict(Counter(s[S.F_THEMA].get("lehrebene") for s in seeds)),
        "themenquelle": dict(Counter(_tp(s).get("quelle") for s in seeds)),
        "status": {k: dict(Counter(_tp(s)["status"][k] for s in seeds if _tp(s).get("status", {}).get(k)))
                   for k in status_keys},
        "belege": dict(Counter(b for s in seeds for b in _tp(s).get("belege", {}).get("erfuellt", []))),
    }


def write(seeds: list[dict], out_path: Path, params: dict) -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for s in seeds:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    st = {"parameter": params, "erzeugt": date.today().isoformat(), **stats(seeds)}
    out_path.with_suffix(".stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return st
