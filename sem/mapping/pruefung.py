"""Prüfliste für unsichere Zuordnungen und redaktionelle Korrekturen.

Die Prüfliste (CSV) enthält nur Zuordnungen, die eine Durchsicht brauchen, mit leeren
Korrekturspalten. Wer Zeilen korrigiert, speichert sie als korrekturen.csv im selben Ordner;
beim nächsten Zusammenführen haben Korrekturen Vorrang vor dem LLM-Ergebnis.
korrektur_codes: Codes mit | getrennt, „-“ = Thema ausschließen. Eine Zeile mit den bisherigen
Codes bestätigt das Ergebnis (Status redaktionell).
"""

from __future__ import annotations

import csv
from pathlib import Path

KORREKTUR_SPALTEN = ["korrektur_codes", "korrektur_lehrebene", "kommentar"]


def schreibe_pruefliste(path: Path, rows: list[dict]):
    spalten = list(dict.fromkeys(k for r in rows for k in r)) if rows else ["id", "grund"]
    spalten += [s for s in KORREKTUR_SPALTEN if s not in spalten]
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=spalten)
        w.writeheader()
        w.writerows(rows)


def lade_korrekturen(path: Path) -> dict[str, dict]:
    """id -> {"codes": [...] | None, "lehrebene": str | None, "ausschliessen": bool, "kommentar": str}.
    Leere Zeilen zählen nicht."""
    if not Path(path).exists():
        return {}
    out = {}
    with Path(path).open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            codes = [c.strip() for c in (r.get("korrektur_codes") or "").split("|") if c.strip()]
            ebene = (r.get("korrektur_lehrebene") or "").strip() or None
            aus = codes == ["-"]
            if r.get("id") and (codes or ebene):
                out[r["id"].strip()] = {"codes": None if aus else codes or None, "lehrebene": ebene,
                                        "ausschliessen": aus, "kommentar": (r.get("kommentar") or "").strip()}
    return out
