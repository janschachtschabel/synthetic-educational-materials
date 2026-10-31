"""Sammelt alle Oberflächentexte und ergänzt fehlende Übersetzungen in i18n/en.json.

    python scripts/i18n_texte.py               # zeigt fehlende Texte (Rückgabewert 1, wenn welche fehlen)
    python scripts/i18n_texte.py --uebersetzen # übersetzt fehlende Texte per LLM (OPENAI_API_KEY)

Quellen: Aufrufe T("…")/t("…") in sem/web/app.py und den Vorlagen (sem/web/templates), die Phasen der
Statusanzeige, Labels und Beschreibungen der Bildungsbereiche (Gleichverteilung,
Filter, Mapping-Schritte), die Feldnamen der Vorschau und die Namen der Ausgabesprachen.
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sem.bereiche import BEREICHE  # noqa: E402
from sem.generate import SPRACHEN  # noqa: E402
from sem.i18n import I18N_DIR  # noqa: E402

EXTRA = ["Seed", "Seeds", "Material", "Materialien", "Modellstandard", "alle Bereiche", "keine"]


WEB = ROOT / "sem" / "web"
_T_AUFRUF = re.compile(r"""\bT\(\s*(["'])(.*?)\1\s*[,)]""", re.S)  # T("…") oder T('…') in den Vorlagen


def aus_app() -> list[str]:
    tree = ast.parse((WEB / "app.py").read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) in ("T", "t") and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            out.append(n.args[0].value)
    from sem.web.app import EFFORTS, GRUPPEN, PHASEN, STATUS_TEXT

    out += [x for ph in PHASEN.values() for x in (ph[0], ph[2])] + [g[1] for g in GRUPPEN]
    out += list(STATUS_TEXT.values()) + EFFORTS + ["Seeds", "Materialien", "Mappings", "Vokabulare"]
    for vorlage in sorted((WEB / "templates").glob("*.html")):
        out += [m.group(2) for m in _T_AUFRUF.finditer(vorlage.read_text(encoding="utf-8"))]
    return out


def aus_vorschau() -> list[str]:
    tree = ast.parse((ROOT / "sem" / "vorschau.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "metadaten")
    return [k.value for n in ast.walk(fn) if isinstance(n, ast.Dict) for k in n.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def aus_bereichen() -> list[str]:
    out = []
    for cls in BEREICHE.values():
        b = cls()
        out.append(b.label)
        out += list(b.balance_options.values())
        for o in b.auswahl_optionen().values():
            out += [o["label"], o["beschreibung"], *o["werte"].values()]
        for s in b.mapping_schritte():
            out += [s.label, s.beschreibung]
    return out


def alle_texte() -> list[str]:
    return list(dict.fromkeys(x for x in aus_app() + aus_vorschau() + aus_bereichen() + list(SPRACHEN.values()) + EXTRA
                              if x and x.strip()))


SYSTEM = """Translate the German user-interface texts of a web app for generating synthetic educational materials into concise British-neutral English.
- Keep placeholders like {name} exactly, keep file names, field names (e.g. cclom:general_language), prefixes (seeds_, materialien_), code values, URLs and Markdown (**bold**).
- Keep German proper names of German institutions, curricula and classifications where no established English name exists, adding nothing.
- Answer only with JSON: {"uebersetzungen": {"<German>": "<English>"}}"""


def main():
    path = I18N_DIR / "en.json"
    alt = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    texte = alle_texte()
    fehlend = [x for x in texte if x not in alt]
    print(f"{len(texte)} Texte, {len(fehlend)} ohne Übersetzung")
    if "--uebersetzen" not in sys.argv:
        for x in fehlend:
            print(" -", x)
        sys.exit(1 if fehlend else 0)  # für CI: Rückgabewert 1, wenn Übersetzungen fehlen
    from sem.llm import LLMClient, LLMConfig

    client = LLMClient(LLMConfig.from_env())
    neu = {}
    for i in range(0, len(fehlend), 40):
        teil = fehlend[i:i + 40]
        res = client.chat_json(SYSTEM, json.dumps(teil, ensure_ascii=False)).get("uebersetzungen", {})
        for x in teil:
            if isinstance(res.get(x), str) and res[x].strip():
                neu[x] = res[x].strip()
        print(f"{min(i + 40, len(fehlend))}/{len(fehlend)}")
    gesamt = {**alt, **neu}
    gesamt = {k: gesamt[k] for k in texte if k in gesamt} | {k: v for k, v in gesamt.items() if k not in texte}
    I18N_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(gesamt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(neu)} neu übersetzt, {len(gesamt)} in {path.name}")


if __name__ == "__main__":
    main()
