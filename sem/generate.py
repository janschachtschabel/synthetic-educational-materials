"""Erzeugt synthetische Bildungsmaterialien aus Seeds.

Pro Seed zwei LLM-Aufrufe:
  1. Volltext aus dem Seed (Prompts volltext_system + volltext)
  2. Titel, Beschreibung, Keywords und Frage-Antwort-Paare nur aus dem Volltext
     (Prompts metadaten_system + titel/beschreibung/keywords/fragen_antworten)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import schema as S
from .bereiche import get as get_bereich
from .llm import LLMClient, LLMConfig, Progress, print_progress, run_tasks
from .prompts import load_prompts, prompt_hash, render


def read_seeds(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def metadata_system(prompts: dict) -> str:
    return render(prompts["metadaten_system"], {
        "prompt_titel": prompts["titel"], "prompt_beschreibung": prompts["beschreibung"],
        "prompt_keywords": prompts["keywords"], "prompt_fragen_antworten": prompts["fragen_antworten"]})


def generate_one(client: LLMClient, seed: dict, prompts_by_bereich: dict, meta: dict, qa_min: int = 8) -> dict:
    bereich = get_bereich(seed[S.F_BEREICH])
    prompts = prompts_by_bereich[bereich.key]
    values = bereich.prompt_values(seed)
    text = client.chat(render(prompts["volltext_system"], values), render(prompts["volltext"], values)).strip()
    if len(text.split()) < 150:
        raise ValueError("Volltext zu kurz")
    user = f"Inhaltstyp: {values.get('inhaltstyp', '')}\n\nVolltext:\n{text}"
    m = client.chat_json(metadata_system(prompts), user)
    qa = [p for p in m.get("frage_antwort_paare", []) if p.get("frage") and p.get("antwort")]
    keywords = list(dict.fromkeys(k.strip() for k in m.get("keywords", []) if k and k.strip()))
    if not m.get("titel") or not m.get("beschreibung") or not keywords or len(qa) < qa_min:
        raise ValueError("Metadaten unvollständig")

    rec = dict(seed)
    rec[S.F_TITLE] = m["titel"].strip()
    rec[S.F_DESCRIPTION] = m["beschreibung"].strip()
    rec[S.F_KEYWORD] = keywords
    rec[S.F_FULLTEXT] = text
    rec[S.F_QA] = qa
    gen = S.provenance("LLM", method="aus dem Volltext erzeugt")
    gen.update({"modell": meta["modell"]})
    prov = rec.setdefault(S.F_PROVENANCE, {})
    prov[S.F_FULLTEXT] = dict(S.provenance("LLM", method="aus dem Seed erzeugt"), modell=meta["modell"])
    for f in (S.F_TITLE, S.F_DESCRIPTION, S.F_KEYWORD, S.F_QA):
        prov[f] = gen
    rec[S.F_GENERATION] = meta
    return rec


def generate(seeds_path: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
             on_progress=print_progress, stop_event=None) -> Progress:
    seeds = read_seeds(seeds_path)
    if limit:
        seeds = seeds[:limit]
    bereiche = {s[S.F_BEREICH] for s in seeds}
    prompts_by_bereich = {b: load_prompts(b) for b in bereiche}
    meta = {"modell": cfg.model, "base_url": cfg.public()["base_url"], "datum": date.today().isoformat(),
            "prompt_hash": {b: prompt_hash(p) for b, p in prompts_by_bereich.items()}}
    return run_tasks(seeds, lambda client, seed: generate_one(client, seed, prompts_by_bereich, meta),
                     Path(out_path), cfg, on_progress=on_progress, stop_event=stop_event)
