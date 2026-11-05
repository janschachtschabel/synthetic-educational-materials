"""Titel fertiger Materialien mit dem aktuellen Titel-Prompt neu erzeugen, ohne die übrigen Felder anzufassen.

    python -m sem titel-neu --materialien output/materialien_….jsonl [--out …] [--limit N] [--workers 20]

Je Material wird nur die Titel-Anfrage der Pipeline gestellt (wie in der Titel-Stufe: Volltext und feste
Metadaten, Titel, Beschreibung und Schlagworte leer). Das Ergebnis kommt in eine neue Datei, die Eingabe bleibt
unverändert. Der bisherige Titel steht danach in synth:titel_alt, Angaben zur Neuerzeugung (Modell, Prompt-Hash,
Tokens) unter synth:generierung.titel_neu. Ist nach FELD_VERSUCHE Versuchen kein Titel brauchbar, bleibt der alte
stehen (titel_neu.behalten nennt den Grund). Fortsetzbar: Ein erneuter Aufruf überspringt fertige Materialien.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import schema as S
from .generate import Material
from .llm import LLMConfig, print_progress, run_tasks
from .prompts import load_prompts, prompt_hash

F_TITEL_ALT = "synth:titel_alt"
_ID = re.compile(rb'\{"id": "([^"\\]*)"')
_BEREICH = re.compile(rb'"' + S.F_BEREICH.encode() + rb'": "(\w+)"')


def standard_ausgabe(materialien: Path) -> Path:
    return materialien.with_name(materialien.stem + "_titel_neu.jsonl")


def _aufgaben(materialien: Path, limit: int | None) -> list[dict]:
    """id, Bereich und Position jeder Zeile; die Datensätze selbst liest der Worker (50k Volltexte im Speicher
    wären mehrere GB)."""
    out, pos = [], 0
    with materialien.open("rb") as f:
        for line in f:
            if (m := _ID.match(line)) and (b := _BEREICH.search(line)):
                out.append({"id": m.group(1).decode("utf-8"), "bereich": b.group(1).decode(), "pos": pos})
            pos += len(line)
            if limit and len(out) >= limit:
                break
    return out


def _lesen(materialien: Path, pos: int) -> dict:
    with materialien.open("rb") as f:
        f.seek(pos)
        return json.loads(f.readline())


def titel_ersetzen(client, rec: dict, prompts_by_bereich: dict, meta: dict) -> dict:
    """Ein Material mit neuem Titel; bleibt nach allen Versuchen keiner brauchbar, der alte."""
    gen = rec.get(S.F_GENERATION) or {}
    m = Material(rec, prompts_by_bereich, inhaltstyp_pruefen=False, ausgabesprache=gen.get("ausgabesprache", "de"),
                 zustand={"felder": {"volltext": rec[S.F_FULLTEXT]}})
    zaehlt = hasattr(client, "zaehler")
    behalten = None
    while "titel" not in m.felder:
        a = m.anfragen()[0]
        if zaehlt:
            client.zaehler(neu=True)
        text = client.chat(a.system, a.user)
        try:
            m.antwort("titel", text, dict(client.zaehler()) if zaehlt else None)
        except ValueError as e:
            behalten = str(e)
            break
    out = dict(rec)
    alt = rec.get(F_TITEL_ALT, rec[S.F_TITLE])  # zweiter Durchlauf: der ursprüngliche Titel bleibt erhalten
    out[F_TITEL_ALT] = alt
    info = {**meta, "prompt_hash": meta["prompt_hash"][m.bereich.key], "neu_angefragt": m.neu.get("titel", 0)}
    if m.tokens.get("aufrufe"):
        info["tokens"] = m.tokens
    if behalten:
        info["behalten"] = behalten
    else:
        out[S.F_TITLE] = m.felder["titel"]
        prov = dict(out.get(S.F_PROVENANCE) or {})
        prov[S.F_TITLE] = dict(S.provenance("LLM", method="aus Volltext und festen Metadaten neu erzeugt (titel)"),
                               modell=meta["modell"], datum=meta["datum"])
        out[S.F_PROVENANCE] = prov
    out[S.F_GENERATION] = {**gen, "titel_neu": info}
    return out


def titel_neu(materialien: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
              on_progress=print_progress, stop_event=None):
    materialien, out_path = Path(materialien), Path(out_path)
    if materialien.resolve() == out_path.resolve():
        raise ValueError("Ausgabe muss eine andere Datei sein als die Eingabe")
    aufgaben = _aufgaben(materialien, limit)
    prompts_by_bereich = {b: load_prompts(b) for b in {a["bereich"] for a in aufgaben}}
    meta = {"modell": cfg.model, "datum": date.today().isoformat(),
            "prompt_hash": {b: prompt_hash(p) for b, p in prompts_by_bereich.items()}}
    if cfg.reasoning_effort:
        meta["reasoning_effort"] = cfg.reasoning_effort
    if cfg.service_tier:
        meta["verarbeitung"] = cfg.service_tier

    def worker(client, aufgabe):
        return titel_ersetzen(client, _lesen(materialien, aufgabe["pos"]), prompts_by_bereich, meta)

    return run_tasks(aufgaben, worker, out_path, cfg, on_progress=on_progress, stop_event=stop_event)
