"""Läufe der Materialerzeugung: was ein Lauf erzeugen soll und ob er sein Ziel erreicht hat.

Jeder Lauf (sofort, Flex oder Batch) schreibt beim Start neben seine Ausgabe eine Lauf-Datei
(output/materialien_<name>.lauf.json) mit Seed-Datei, Zielzahl und Einstellungen. Bricht ein Lauf ab (z. B. weil
das Internet weg war) oder bleiben Materialien fehlgeschlagen, steht er in unfertige_laeufe(): Oberfläche und
Kommandozeile können ihn mit denselben Einstellungen fortsetzen oder einen neuen Lauf unter anderem Namen starten.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .llm import LLMConfig, read_done
from .paths import OUTPUT_DIR, PRAEFIX


def lauf_pfad(out_path: Path) -> Path:
    return Path(out_path).with_suffix(".lauf.json")


def lauf_speichern(out_path: Path, seeds_path: Path, cfg: LLMConfig, limit: int | None, inhaltstyp_pruefen: bool,
                   ausgabesprache: str, verarbeitung: str):
    """Hält fest, was der Lauf erzeugen soll. verarbeitung: "sofort", "flex" oder "batch"."""
    seeds_path = Path(seeds_path)
    with seeds_path.open(encoding="utf-8") as f:
        n_seeds = sum(1 for z in f if z.strip())
    p = lauf_pfad(out_path)
    alt = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    lauf = {"seeds": str(seeds_path.resolve()), "limit": limit, "ziel": min(limit, n_seeds) if limit else n_seeds,
            "ausgabesprache": ausgabesprache, "inhaltstyp_pruefen": inhaltstyp_pruefen, "verarbeitung": verarbeitung,
            "modell": cfg.model, "reasoning_effort": cfg.reasoning_effort,
            "gestartet": alt.get("gestartet") or datetime.now().isoformat(timespec="seconds"),
            "zuletzt": datetime.now().isoformat(timespec="seconds")}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lauf, ensure_ascii=False, indent=1), encoding="utf-8")


def lauf_stand(out_path: Path) -> dict | None:
    """Lauf-Datei samt Stand: fertig, fehlgeschlagen (letzter Versuch), gesperrt (Sperrdatei vorhanden)."""
    out_path = Path(out_path)
    p = lauf_pfad(out_path)
    if not p.exists():
        return None
    lauf = json.loads(p.read_text(encoding="utf-8"))
    fertig = len(read_done(out_path))
    err = out_path.with_suffix(".fehler.jsonl")
    fehl = len(read_done(err) - read_done(out_path)) if err.exists() else 0
    return {**lauf, "name": out_path.name, "ausgabe": str(out_path), "fertig": fertig, "fehlgeschlagen": fehl,
            "gesperrt": out_path.with_suffix(".lock").exists(), "ziel_erreicht": fertig >= lauf["ziel"]}


def unfertige_laeufe(ordner: Path = OUTPUT_DIR) -> list[dict]:
    """Läufe, die ihr Ziel nicht erreicht haben, zuletzt bearbeitete zuerst."""
    out = []
    for p in Path(ordner).glob(f"{PRAEFIX['materialien']}*.lauf.json"):
        st = lauf_stand(p.with_name(p.name.removesuffix(".lauf.json") + ".jsonl"))
        if st and not st["ziel_erreicht"]:
            out.append(st)
    return sorted(out, key=lambda s: s["zuletzt"], reverse=True)


def sperre_aufheben(out_path: Path):
    """Sperrdatei eines abgebrochenen Laufs entfernen (nur, wenn sicher kein Lauf mehr auf der Datei arbeitet)."""
    Path(out_path).with_suffix(".lock").unlink(missing_ok=True)


def freier_name(out_path: Path) -> Path:
    """Nächster unbenutzter Name für einen neuen Lauf: materialien_x.jsonl → materialien_x_2.jsonl, _3, …"""
    out_path = Path(out_path)
    stamm, i = out_path.stem, 2
    while True:
        p = out_path.with_name(f"{stamm}_{i}.jsonl")
        if not p.exists() and not lauf_pfad(p).exists():
            return p
        i += 1
