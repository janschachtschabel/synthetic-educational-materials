"""Kommandozeile.

    python -m sem info
    python -m sem seeds --anzahl hochschule=30000 [--gleichverteilung hochschule=thema] [--seed 42] [--out …]
    python -m sem materialien --seeds output/seeds.jsonl [--out …] [--limit N] [--workers 20] [--model …]
    python -m sem mappings hochschule [alle|schritt …] [--limit N] [--stimmen 3] [--embedding-modell …]
    python -m sem mappings hochschule --liste
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .paths import OUTPUT_DIR


def _pairs(values: list[str], cast=str) -> dict:
    out = {}
    for v in values or []:
        k, _, x = v.partition("=")
        if not x:
            raise SystemExit(f"Erwartet bereich=wert, nicht {v!r}")
        out[k.strip()] = cast(x.strip())
    return out


def _llm_args(p):
    g = p.add_argument_group("LLM (Standard aus OPENAI_BASE_URL, OPENAI_API_KEY, LLM_MODEL, LLM_WORKERS)")
    g.add_argument("--base-url")
    g.add_argument("--api-key")
    g.add_argument("--model")
    g.add_argument("--workers", type=int)
    g.add_argument("--retries", type=int, dest="max_retries")


def _cfg(a):
    from .llm import LLMConfig

    return LLMConfig.from_env(base_url=a.base_url, api_key=a.api_key, model=a.model, workers=a.workers,
                              max_retries=a.max_retries)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sem", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="Bildungsbereiche, Themenzahl und Gleichverteilungsfelder anzeigen")

    p = sub.add_parser("seeds", help="Seeds erzeugen (ohne LLM)")
    p.add_argument("--anzahl", nargs="+", required=True, metavar="BEREICH=N")
    p.add_argument("--gleichverteilung", nargs="*", metavar="BEREICH=FELD")
    p.add_argument("--seed", type=int, default=42, help="Zufalls-Seed (gleicher Wert = gleiche Seeds)")
    p.add_argument("--out", type=Path)

    p = sub.add_parser("materialien", help="Materialien aus Seeds erzeugen (LLM, fortsetzbar)")
    p.add_argument("--seeds", type=Path, required=True)
    p.add_argument("--out", type=Path)
    p.add_argument("--limit", type=int)
    _llm_args(p)

    p = sub.add_parser("mappings", help="Mappings eines Bildungsbereichs neu bauen (Schritte siehe --liste)")
    p.add_argument("bereich")
    p.add_argument("schritte", nargs="*", default=["alle"], help="Schritte in dieser Reihenfolge, Standard: alle")
    p.add_argument("--liste", action="store_true", help="Schritte des Bereichs anzeigen")
    p.add_argument("--limit", type=int, help="nur die ersten N Themen (zum Testen)")
    p.add_argument("--stimmen", type=int, default=3, help="LLM-Stimmen je Zuordnung")
    p.add_argument("--embedding-modell", help="lokales Embedding-Modell (Standard intfloat/multilingual-e5-small)")
    p.add_argument("--pro-fach", type=int, default=12, help="Grundlagenthemen je Studienfach")
    _llm_args(p)

    a = ap.parse_args(argv)

    if a.cmd == "info":
        from .bereiche import BEREICHE

        for key, cls in BEREICHE.items():
            print(json.dumps(cls().describe(), ensure_ascii=False, indent=1))

    elif a.cmd == "seeds":
        from . import seeds

        counts = _pairs(a.anzahl, int)
        balance = _pairs(a.gleichverteilung)
        out = a.out or OUTPUT_DIR / ("seeds_" + "_".join(f"{k}{v}" for k, v in counts.items()) + f"_s{a.seed}.jsonl")
        st = seeds.write(seeds.build(counts, balance, a.seed), out,
                         {"anzahl": counts, "gleichverteilung": balance, "zufalls_seed": a.seed})
        print(f"{st['anzahl']} Seeds → {out}")
        print(json.dumps({k: st[k] for k in ("themen_abgedeckt", "seeds_je_gruppe", "seeds_je_thema", "niveau")},
                         ensure_ascii=False))

    elif a.cmd == "materialien":
        from .generate import generate

        out = a.out or a.seeds.with_name(a.seeds.stem.replace("seeds", "materialien") + ".jsonl")
        prog = generate(a.seeds, out, _cfg(a), limit=a.limit)
        print(f"{prog.done} neu, {prog.skipped} schon vorhanden, {prog.failed} fehlgeschlagen → {out}")
        if prog.status != "fertig":
            sys.exit(2)

    elif a.cmd == "mappings":
        from .bereiche import get
        from .llm import print_progress

        b = get(a.bereich)
        schritte = {x.key: x for x in b.mapping_schritte()}
        if a.liste or not schritte:
            for x in schritte.values():
                print(f"{x.key:16} {'LLM ' if x.llm else '    '}{x.beschreibung}")
            print("alle = " + " → ".join(b.mapping_folge))
            return
        keys = b.mapping_folge if a.schritte == ["alle"] else a.schritte
        unbekannt = [k for k in keys if k not in schritte]
        if unbekannt:
            raise SystemExit(f"Unbekannte Schritte {unbekannt}. Möglich: {', '.join(schritte)}, alle")
        prog = b.mapping_ausfuehren(keys, _cfg(a), on_progress=print_progress, limit=a.limit, stimmen=a.stimmen,
                                    embedding_modell=a.embedding_modell, pro_fach=a.pro_fach)
        print("\n" + prog.line())
        if "fertig" not in prog.status:
            sys.exit(2)

if __name__ == "__main__":
    main()
