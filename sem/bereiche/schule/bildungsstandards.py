"""KMK-Bildungsstandards als Metadaten der Schulthemen (Deutsch, Mathematik, erste Fremdsprache, Biologie,
Chemie, Physik).

Die Gliederung (Kompetenzbereiche, Leitideen, Basiskonzepte, Teilbereiche; nur Überschriften, keine
Standardformulierungen) liegt in bildungsstandards_gliederung.json. Je Unterrichtsthema und passendem Dokument
(Fach und Schulstufe: Primarstufe → Primarbereich, Sek I → ESA/MSA, Sek II → AHR) wählen drei Stimmen ein bis
zwei inhaltliche Einträge. Prozessbezogene Kompetenzen gelten für alle Themen eines Fachs und werden nicht
zugeordnet.

Schritte: bista (Abstimmung), bista_zusammenfuehren (bildungsstandards.jsonl).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from functools import cache

from ...llm import LLMConfig
from ...mapping import abstimmung
from ...mapping.abstimmung import Ziel
from ...paths import DATA_DIR
from ..base import fertig
from . import DIR, load_jsonl

GLIEDERUNG_PATH = DIR / "bildungsstandards_gliederung.json"
ZUORDNUNG_PATH = DIR / "bildungsstandards.jsonl"
STIMMEN_PATH = DATA_DIR / "cache" / "schule_stimmen_bista.jsonl"
N_STIMMEN = 3
INHALTLICH = {"kompetenzbereich", "leitidee", "basiskonzept", "teilbereich"}
# Fachportale mit Bildungsstandards (Englisch und Französisch: erste Fremdsprache); Schulstufe → Abschluss
PORTALE = {"Deutsch", "Mathematik", "Englisch", "Französisch", "Biologie", "Chemie", "Physik"}
STUFE_ABSCHLUSS = {"primar": ["primar"], "sek1": ["msa", "esa"], "sek2": ["ahr"]}


@cache
def gliederung() -> dict:
    return json.loads(GLIEDERUNG_PATH.read_text(encoding="utf-8"))


def dokument(portal: str, stufe: str) -> dict | None:
    """Das Bildungsstandard-Dokument zu Fachportal und Schulstufe, sonst None."""
    if portal not in PORTALE:
        return None
    g = gliederung()
    zeile = next((v for k, v in g["meta"]["faecher"].items() if portal.lower() in k.lower()), {})
    key = next((zeile[a] for a in STUFE_ABSCHLUSS[stufe] if zeile.get(a)), None)
    return next((q for q in g["quellen"] if q["key"] == key), None)


def eintraege(key: str) -> list[dict]:
    return [e for e in gliederung()["eintraege"] if e["quelle"] == key and e["typ"] in INHALTLICH]


def _objekte() -> list[dict]:
    out = []
    for t in load_jsonl(DIR / "themen_schule.jsonl"):
        if t["art"] != "unterrichtsthema":
            continue
        for stufe in t["stufen"]:
            d = dokument(t["portal"], stufe)
            if d:
                out.append({"id": f"{t['id']}|{d['key']}", "thema": t["id"], "dokument": d["key"],
                            "prompt": {"thema": t["label_de"], "fach": t["portal"],
                                       "pfad": " > ".join(t["pfad"]) or "-", "keywords": t.get("keywords", [])[:6],
                                       "bildungsstandards": d["name"]},
                            "text": t["label_de"]})
    return out


REGELN = """Zu jedem Unterrichtsthema gibt es die inhaltliche Gliederung der passenden KMK-Bildungsstandards (Code | Bereich | übergeordnete Überschrift).
- Wähle 1 bis 2 Einträge, zu denen das Thema im Unterricht gehört, den passendsten zuerst. Gib als "code" den Code an.
- Bevorzuge den genauesten Eintrag (Teilbereich vor Kompetenzbereich), wenn er eindeutig passt.
- Passt kein Eintrag, gib eine leere Auswahl.
- Je Eintrag: "begruendung" (ein kurzer Satz) und "belegbegriffe": 1 bis 3 Begriffe aus Thema, Pfad oder Keywords."""

PERSPEKTIVEN = [
    "Du bist Fachleiterin in der Lehrkräfteausbildung und kennst die KMK-Bildungsstandards.",
    "Du entwickelst Aufgaben für die Vergleichsarbeiten nach den Bildungsstandards der KMK.",
    "Du bist erfahrene Lehrkraft und planst Unterricht nach den Bildungsstandards.",
]


def build_bista(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = N_STIMMEN, **_):
    objekte = _objekte()
    ziele = {e["code"]: Ziel(e["code"], e["code"], e["label"], " > ".join(e["pfad"][-1:]))
             for e in gliederung()["eintraege"] if e["typ"] in INHALTLICH}
    kand = {o["id"]: [e["code"] for e in eintraege(o["dokument"])] for o in objekte}
    systeme = [PERSPEKTIVEN[i % len(PERSPEKTIVEN)] + "\n\n" + REGELN for i in range(stimmen)]
    return abstimmung.abstimmen(objekte, kand, ziele, systeme, STIMMEN_PATH, cfg, max_auswahl=2, batch=12,
                                on_progress=on_progress, stop_event=stop_event)


METHODE = "LLM-Mehrfachabstimmung über die inhaltliche Gliederung der passenden KMK-Bildungsstandards"


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    st = abstimmung.lade_stimmen(STIMMEN_PATH)
    by_code = {e["code"]: e for e in gliederung()["eintraege"]}
    rows = []
    for o in _objekte():
        if o["id"] not in st:
            continue
        a = abstimmung.auswerten(st[o["id"]], stimmen, o["text"], max_auswahl=2)
        if not a["details"] or a["details"][0]["stimmen"] < 2:  # keine Mehrheit für einen Eintrag
            continue
        rows.append({"id": o["id"], "thema": o["thema"], "dokument": o["dokument"],
                     "eintraege": [{"code": d["code"], "label": by_code[d["code"]]["label"],
                                    "typ": by_code[d["code"]]["typ"], "pfad": by_code[d["code"]]["pfad"],
                                    "stimmen": d["stimmen"]} for d in a["details"] if d["code"] in by_code],
                     "status": a["status"]})
    meta = {"methode": METHODE, "modell": modell or (cfg.model if cfg else None), "stimmen": stimmen,
            "datum": date.today().isoformat()}
    ZUORDNUNG_PATH.write_text("".join(json.dumps({**r, "mapping": meta}, ensure_ascii=False) + "\n" for r in rows),
                              encoding="utf-8")
    return fertig(f"{len(rows)} Zuordnungen Thema → Bildungsstandards ({dict(Counter(r['status'] for r in rows))})")


@cache
def zuordnungen() -> dict[str, dict]:
    """„Themen-ID|Dokument“ → Zuordnung."""
    return {r["id"]: r for r in load_jsonl(ZUORDNUNG_PATH)} if ZUORDNUNG_PATH.exists() else {}


def fuer_seed(thema_id: str, portal: str, stufe: str) -> dict | None:
    """Metadaten synth:bildungsstandards für einen Seed, oder None."""
    d = dokument(portal, stufe)
    z = zuordnungen().get(f"{thema_id}|{d['key']}") if d else None
    if not z:
        return None
    return {"dokument": d["name"], "beschluss": d["beschluss"], "uri": d["uri"],
            "eintraege": [{k: e[k] for k in ("code", "label", "typ", "pfad")} for e in z["eintraege"]],
            "status": z["status"]}
