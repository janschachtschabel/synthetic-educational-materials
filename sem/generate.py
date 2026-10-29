"""Erzeugt synthetische Bildungsmaterialien aus Seeds.

Pro Seed nacheinander:
  1. Volltext aus dem Seed (volltext_system + volltext)
  2. Titel, Beschreibung, Keywords: je ein Aufruf mit gemeinsamem Systemprompt (system) und
     gemeinsamer Materialvorlage (material); der Auftrag kommt aus der Felddatei. Jeder Aufruf
     sieht den Volltext, die festen Metadaten des Seeds und die schon erzeugten Felder.
  3. gleichzeitig: Inhaltstyp-Prüfung (der Inhaltstyp wird ohne Kenntnis des Seed-Werts aus dem
     fertigen Material bestimmt und mit dem Seed verglichen, nur protokolliert) und
     Frage-Antwort-Paare (JSON)
Liefert ein Feld nichts Brauchbares, wird nur dieses Feld erneut angefragt. Dauer und
Neuanfragen je Feld stehen in synth:generierung.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import schema as S
from .bereiche import get as get_bereich
from .llm import LLMClient, LLMConfig, Progress, print_progress, run_tasks
from .prompts import load_prompts, prompt_hash, render

LEER = "-"
FELD_VERSUCHE = 3
_QUOTES = "\"'„“”«»‚‘’`"


def read_seeds(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# --- Prüfen der Antworten ---------------------------------------------------------------------

def _einzeilig(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTES).strip()


def parse_titel(text: str) -> str:
    t = _einzeilig(_einzeilig(text.strip().splitlines()[0] if text.strip() else "").rstrip("."))
    if not 10 <= len(t) <= 120:
        raise ValueError(f"Titel mit {len(t)} Zeichen")
    return t


def parse_beschreibung(text: str) -> str:
    t = _einzeilig(text)
    if not 250 <= len(t) <= 1200:
        raise ValueError(f"Beschreibung mit {len(t)} Zeichen")
    return t


def parse_keywords(text: str, minimum: int = 4, maximum: int = 8) -> list[str]:
    roh = [_einzeilig(k.lstrip("-•*0123456789. ")) for k in re.split(r"[,;\n]", text)]
    out: dict[str, str] = {}
    for k in roh:
        if k and len(k) <= 60:
            out.setdefault(k.casefold(), k)
    if len(out) < minimum:
        raise ValueError(f"{len(out)} Keywords")
    return list(out.values())[:maximum]


def parse_inhaltstyp(text: str, kategorien: list[dict]) -> dict:
    t = _einzeilig(text).casefold()
    for k in kategorien:
        if t in (k["id"], k["name"].casefold()):
            return k
    raise ValueError(f"unbekannte Kategorie {t[:40]!r}")


def parse_paare(data: dict | list, minimum: int) -> list[dict]:
    paare = data.get("paare", data.get("frage_antwort_paare", [])) if isinstance(data, dict) else data
    qa = [{k: str(p[k]).strip() for k in ("frage", "antwort", "typ") if p.get(k)}
          for p in paare if isinstance(p, dict) and p.get("frage") and p.get("antwort")]
    if len(qa) < minimum:
        raise ValueError(f"{len(qa)} Frage-Antwort-Paare")
    return qa


def frage(client: LLMClient, system: str, user: str, parse, json_mode: bool = False,
          versuche: int = FELD_VERSUCHE, protokoll: dict | None = None, name: str = ""):
    """Fragt ein Feld an und prüft die Antwort; unbrauchbare Antworten werden neu angefragt.
    protokoll[name] zählt die Neuanfragen."""
    fehler = None
    for i in range(versuche):
        try:
            return parse(client.chat_json(system, user) if json_mode else client.chat(system, user))
        except ValueError as e:  # auch json.JSONDecodeError
            fehler = e
            if protokoll is not None:
                protokoll[name] = i + 1
    raise ValueError(f"{name or 'Feld'} nach {versuche} Versuchen unbrauchbar: {fehler}")


# --- Ein Material -----------------------------------------------------------------------------

def material_user(prompts: dict, values: dict, felder: dict, text: str, auftrag: str,
                  inhaltstyp: bool = True) -> str:
    v = {k: values.get(k) or LEER for k in ("bildungsstufe", "fach", "zielgruppe", "sprache")}
    v["inhaltstyp"] = (values.get("inhaltstyp") or LEER) if inhaltstyp else LEER
    v["titel"] = felder.get("titel") or LEER
    v["beschreibung"] = felder.get("beschreibung") or LEER
    v["keywords"] = ", ".join(felder.get("keywords") or []) or LEER
    v.update(volltext=text, auftrag=auftrag)
    return render(prompts["material"], v)


def generate_one(client: LLMClient, seed: dict, prompts_by_bereich: dict, meta: dict, qa_min: int = 8,
                 inhaltstyp_pruefen: bool = True) -> dict:
    bereich = get_bereich(seed[S.F_BEREICH])
    prompts = prompts_by_bereich[bereich.key]
    values = bereich.prompt_values(seed)
    start = time.time()
    neu: dict[str, int] = {}
    for i in range(FELD_VERSUCHE):
        text = client.chat(render(prompts["volltext_system"], values), render(prompts["volltext"], values)).strip()
        if len(text.split()) >= 150:
            break
        neu["volltext"] = i + 1
    else:
        raise ValueError(f"Volltext nach {FELD_VERSUCHE} Versuchen zu kurz")

    system = render(prompts["system"], values)
    felder: dict = {}
    # Titel, Beschreibung, Keywords nacheinander: jedes Feld sieht die vorherigen
    for name, parse in (("titel", parse_titel), ("beschreibung", parse_beschreibung), ("keywords", parse_keywords)):
        auftrag = render(prompts[name], values)
        felder[name] = frage(client, system, material_user(prompts, values, felder, text, auftrag), parse,
                             protokoll=neu, name=name)

    # Inhaltstyp-Prüfung und Frage-Antwort-Paare hängen nicht voneinander ab: gleichzeitig
    def inhaltstyp():
        kategorien = bereich.inhaltstyp_kategorien() if inhaltstyp_pruefen else []
        if not kategorien:
            return None
        auftrag = render(prompts["inhaltstyp"], {
            "kategorien": "\n".join(f"- {k['name']} → {k['id']}" for k in kategorien),
            "beispiel_id": kategorien[0]["id"]})
        erkannt = frage(client, system, material_user(prompts, values, felder, text, auftrag, inhaltstyp=False),
                        lambda t: parse_inhaltstyp(t, kategorien), protokoll=neu, name="inhaltstyp")
        erwartet = seed.get(S.F_LRT, [None])[0]
        return {"erwartet": erwartet, "erkannt": erkannt["uri"], "erkannt_label": erkannt["name"],
                "uebereinstimmung": erkannt["uri"] == erwartet}

    def fragen():
        auftrag = render(prompts["fragen_antworten"], values)
        return frage(client, system, material_user(prompts, values, felder, text, auftrag),
                     lambda d: parse_paare(d, qa_min), json_mode=True, protokoll=neu, name="fragen_antworten")

    with ThreadPoolExecutor(2) as pool:
        f_typ, f_qa = pool.submit(inhaltstyp), pool.submit(fragen)
        pruefung, qa = f_typ.result(), f_qa.result()

    rec = dict(seed)
    rec[S.F_TITLE] = felder["titel"]
    rec[S.F_DESCRIPTION] = felder["beschreibung"]
    rec[S.F_KEYWORD] = felder["keywords"]
    rec[S.F_FULLTEXT] = text
    rec[S.F_QA] = qa
    if pruefung:
        rec[S.F_LRT_CHECK] = pruefung
    prov = rec.setdefault(S.F_PROVENANCE, {})
    prov[S.F_FULLTEXT] = dict(S.provenance("LLM", method="aus dem Seed erzeugt (volltext)"), modell=meta["modell"])
    for f, datei in ((S.F_TITLE, "titel"), (S.F_DESCRIPTION, "beschreibung"), (S.F_KEYWORD, "keywords"),
                     (S.F_QA, "fragen_antworten")):
        prov[f] = dict(S.provenance("LLM", method=f"aus Volltext und festen Metadaten erzeugt ({datei})"),
                       modell=meta["modell"])
    rec[S.F_GENERATION] = {**meta, "dauer_sekunden": round(time.time() - start, 1), "neu_angefragt": neu}
    return rec


def generate(seeds_path: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
             inhaltstyp_pruefen: bool = True, on_progress=print_progress, stop_event=None) -> Progress:
    seeds = read_seeds(seeds_path)
    if limit:
        seeds = seeds[:limit]
    bereiche = {s[S.F_BEREICH] for s in seeds}
    prompts_by_bereich = {b: load_prompts(b) for b in bereiche}
    meta = {"modell": cfg.model, "base_url": cfg.public()["base_url"], "datum": date.today().isoformat(),
            "prompt_hash": {b: prompt_hash(p) for b, p in prompts_by_bereich.items()},
            "inhaltstyp_pruefung": inhaltstyp_pruefen, **({"reasoning_effort": cfg.reasoning_effort}
                                                           if cfg.reasoning_effort else {})}
    return run_tasks(seeds, lambda client, seed: generate_one(client, seed, prompts_by_bereich, meta,
                                                              inhaltstyp_pruefen=inhaltstyp_pruefen),
                     Path(out_path), cfg, on_progress=on_progress, stop_event=stop_event)
