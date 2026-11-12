"""Schablonenhafte Anfänge der Beschreibungen fertiger Materialien ersetzen, ohne den Rest anzufassen.

    python -m sem beschreibung-einstieg --materialien output/materialien_….jsonl [--out …] [--limit N]

Im 50k-Datensatz begann gut die Hälfte der Beschreibungen mit „Im Mittelpunkt …“ oder „Das Material …“. Für jedes
Material, dessen Beschreibung so beginnt (generate.beschreibung_floskel), wird nur der erste Satz neu formuliert
(prompts/beschreibung_einstieg.md: bisheriger erster Satz, die folgenden Sätze und der Titel als Kontext, kein
Volltext). Die übrigen Sätze bleiben Zeichen für Zeichen gleich. Materialien ohne Schablone werden unverändert
übernommen. Das Ergebnis kommt in eine neue Datei; die bisherige Beschreibung steht in synth:beschreibung_alt,
Angaben zur Änderung unter synth:generierung.beschreibung_einstieg. Ist nach FELD_VERSUCHE Versuchen kein Satz
brauchbar, bleibt die Beschreibung, wie sie war (Grund unter …beschreibung_einstieg.behalten). Fortsetzbar wie
titel-neu.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import schema as S
from .generate import (BESCHREIBUNG_MAX, BESCHREIBUNG_MIN, FELD_VERSUCHE, _FORM_WOERTER, _einzeilig,
                       beschreibung_floskel)
from .llm import LLMConfig, print_progress, run_tasks
from .paths import PROMPT_DIR
from .prompts import load_prompts, prompt_hash, render
from .titel_neu import _aufgaben, _lesen

F_BESCHREIBUNG_ALT = "synth:beschreibung_alt"
PROMPT = "beschreibung_einstieg"
_SATZENDE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ0-9„\"(])")
# Materialtyp als Satzgegenstand am Anfang („Das Arbeitsblatt …“, „Die Präsentation …“, „Der Lernpfad …“)
_TYP_ANFANG = re.compile(r"^(der|die|das|ein|eine|dieser|diese|dieses)\s+([\wäöüß-]+)", re.I)
_TYPEN_EXTRA = {"lernpfad", "projekt", "übungsmaterial", "material", "einheit", "unterrichtseinheit", "kurs",
                "kurseinheit", "folge", "foliensatz", "vortrag", "fallstudie", "klausur", "musterlösung", "artikel",
                "lehrbuchkapitel", "kapitel", "beitrag", "text", "transkript", "rollenspiel", "stationenlernen",
                "experiment", "versuchsanleitung", "lernsituation", "handreichung", "unterweisung"}


def standard_ausgabe(materialien: Path) -> Path:
    return materialien.with_name(materialien.stem + "_einstieg.jsonl")


def teilen(beschreibung: str) -> tuple[str, str]:
    """(erster Satz, Rest); der Rest bleibt unverändert."""
    teile = _SATZENDE.split(beschreibung.strip(), maxsplit=1)
    return teile[0], (teile[1] if len(teile) > 1 else "")


def parse_satz(text: str) -> str:
    s = _einzeilig(text.strip().splitlines()[0] if text.strip() else "")
    if not 25 <= len(s) <= 300:
        raise ValueError(f"Satz mit {len(s)} Zeichen")
    if s[-1] not in ".!?":
        raise ValueError("Satz ohne Satzende")
    if len(_SATZENDE.split(s)) > 1:
        raise ValueError("mehr als ein Satz")
    if len(s.split()) > 25:
        raise ValueError(f"Satz mit {len(s.split())} Wörtern")
    if f := beschreibung_floskel(s):
        raise ValueError(f"Satz beginnt mit {f!r}")
    if (m := _TYP_ANFANG.match(s)) and m.group(2).casefold() in _FORM_WOERTER | _TYPEN_EXTRA:
        raise ValueError(f"Satz beginnt mit dem Materialtyp {m.group(2)!r}")
    return s


def einstieg_ersetzen(client, rec: dict, prompts_by_bereich: dict, vorlage: str, meta: dict) -> dict:
    """Ein Material mit neuem ersten Satz der Beschreibung; ohne Schablone unverändert."""
    alt = rec.get(F_BESCHREIBUNG_ALT, rec[S.F_DESCRIPTION])  # zweiter Durchlauf: ursprüngliche Fassung bleibt
    floskel = beschreibung_floskel(rec[S.F_DESCRIPTION])
    if not floskel:
        return rec
    erster, rest = teilen(rec[S.F_DESCRIPTION])
    p = prompts_by_bereich[rec[S.F_BEREICH]]
    system = render(p["system"], {})
    user = render(vorlage, {"floskel": floskel, "titel": rec.get(S.F_TITLE, ""), "erster_satz": erster,
                            "rest": rest or "-"})
    zaehlt = hasattr(client, "zaehler")
    tokens: dict[str, int] = {}
    neu, behalten, satz = 0, None, None
    while satz is None:
        if zaehlt:
            client.zaehler(neu=True)
        text = client.chat(system, user)
        if zaehlt:
            for k, n in dict(client.zaehler()).items():
                tokens[k] = tokens.get(k, 0) + n
        try:
            satz = parse_satz(text)
            neu_text = f"{satz} {rest}".strip()
            # Grenzen wie bei der Erzeugung; eine schon vorher kürzere oder längere Beschreibung darf es bleiben
            if not min(BESCHREIBUNG_MIN, int(0.8 * len(alt))) <= len(neu_text) <= max(BESCHREIBUNG_MAX, len(alt)):
                satz = None
                raise ValueError(f"Beschreibung mit {len(neu_text)} Zeichen")
        except ValueError as e:
            neu += 1
            if neu >= FELD_VERSUCHE:
                behalten = str(e)
                break
    out = dict(rec)
    gen = rec.get(S.F_GENERATION) or {}
    info = {**meta, "prompt_hash": meta["prompt_hash"], "neu_angefragt": neu, "floskel": floskel}
    if tokens.get("aufrufe"):
        info["tokens"] = tokens
    if behalten:
        info["behalten"] = behalten
    else:
        out[F_BESCHREIBUNG_ALT] = alt
        out[S.F_DESCRIPTION] = f"{satz} {rest}".strip()
        prov = dict(out.get(S.F_PROVENANCE) or {})
        prov[S.F_DESCRIPTION] = dict(prov.get(S.F_DESCRIPTION) or {},
                                     einstieg=dict(S.provenance("LLM", method="erster Satz neu formuliert "
                                                                "(beschreibung_einstieg), übrige Sätze unverändert"),
                                                   modell=meta["modell"], datum=meta["datum"]))
        out[S.F_PROVENANCE] = prov
    out[S.F_GENERATION] = {**gen, "beschreibung_einstieg": info}
    return out


def beschreibung_einstieg(materialien: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
                          on_progress=print_progress, stop_event=None):
    materialien, out_path = Path(materialien), Path(out_path)
    if materialien.resolve() == out_path.resolve():
        raise ValueError("Ausgabe muss eine andere Datei sein als die Eingabe")
    aufgaben = _aufgaben(materialien, limit)
    prompts_by_bereich = {b: load_prompts(b) for b in {a["bereich"] for a in aufgaben}}
    vorlage = (PROMPT_DIR / f"{PROMPT}.md").read_text(encoding="utf-8").strip()
    meta = {"modell": cfg.model, "datum": date.today().isoformat(),
            "prompt_hash": prompt_hash({PROMPT: vorlage, "system": prompts_by_bereich[next(iter(prompts_by_bereich))]
                                        ["system"]}) if prompts_by_bereich else ""}
    if cfg.reasoning_effort:
        meta["reasoning_effort"] = cfg.reasoning_effort
    if cfg.service_tier:
        meta["verarbeitung"] = cfg.service_tier

    def worker(client, aufgabe):
        return einstieg_ersetzen(client, _lesen(materialien, aufgabe["pos"]), prompts_by_bereich, vorlage, meta)

    return run_tasks(aufgaben, worker, out_path, cfg, on_progress=on_progress, stop_event=stop_event)
