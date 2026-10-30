"""Lernfelder der KMK-Rahmenlehrpläne als zweite Themenquelle der Berufsbildung.

Schritte (Folge „lernfeld“, siehe METHODIK.md):
  lernfelder      Markdown-Fassungen der Rahmenlehrpläne einlesen (--rlp ORDNER oder RLP_MARKDOWN_DIR), nur
                  Pläne der aktuellen KMK-Downloadliste; je Lernfeld Nummer, Titel, Ausbildungsjahr, Zeitrichtwert
  rlp_kandidaten  je Rahmenlehrplan die ähnlichsten ESCO-Berufe (lokale Embeddings)
  rlp_berufe      drei Stimmen: welche ESCO-Berufe dem deutschen Ausbildungsberuf entsprechen
  rlp_zusammenfuehren  rahmenlehrplaene.jsonl, pruefliste_rahmenlehrplaene.csv; korrekturen_rahmenlehrplaene.csv
                  hat Vorrang (korrektur_codes: ESCO-Kürzel mit | getrennt, „-“ = Plan ausschließen)
Ziel- und Inhaltstexte der Lernfelder werden nicht übernommen.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections import Counter
from datetime import date
from pathlib import Path

from ...llm import LLMConfig
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder, top_k
from ...paths import DATA_DIR, lokal_pfad
from ...quellen import rahmenlehrplaene as rlp
from ..base import fertig
from . import DIR, load_jsonl

CACHE = DATA_DIR / "cache"
ROH_PATH = CACHE / "kmk" / "lernfelder_roh.jsonl"
KANDIDATEN_PATH = CACHE / "berufsbildung_rlp_kandidaten.json"
STIMMEN_PATH = CACHE / "berufsbildung_stimmen_rlp.jsonl"
PLAENE = "berufsbildung/rahmenlehrplaene.jsonl"  # unter data/lokal/, nicht im Repo
PRUEFLISTE_PATH = DIR / "pruefliste_rahmenlehrplaene.csv"
KORREKTUREN_PATH = DIR / "korrekturen_rahmenlehrplaene.csv"
N_KANDIDATEN = 12
N_STIMMEN = 3


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _code(uri: str) -> str:
    """Kurzes, stabiles Kürzel eines ESCO-Berufs für Prompt und Korrekturen."""
    return uri.rsplit("/", 1)[-1][:8]


# --- 1. Lernfelder einlesen ------------------------------------------------------------------------

def titel_ok(titel: str) -> bool:
    """Kein Umwandlungsrest wie „(SM)“ (Kürzel der Fachrichtung) oder „Zielformulierung :“ (Abschnittskopf)."""
    t = titel.strip()
    return len(t) > 4 and not re.fullmatch(r"\(?[A-Za-z]{1,4}\)?", t) and not t.lower().startswith("zielformulierung")


def build_lernfelder(cfg: LLMConfig | None = None, rlp_ordner: str | None = None, **_):
    ordner = Path(rlp_ordner or os.environ.get("RLP_MARKDOWN_DIR") or "")
    if not rlp_ordner and not os.environ.get("RLP_MARKDOWN_DIR"):
        raise FileNotFoundError("Ordner mit den Markdown-Fassungen fehlt: --rlp ORDNER oder RLP_MARKDOWN_DIR setzen.")
    dateien = {p.name.removesuffix("_converted.md").removesuffix(".md").removesuffix(".pdf"): p
               for p in ordner.glob("*.md")}
    rows, fehlt = [], []
    for e in rlp.downloadliste():
        p = dateien.get((e["datei"] or "").removesuffix(".pdf"))
        if not p:
            fehlt.append(e["titel"])
            continue
        lf = rlp.lernfelder(p.read_text(encoding="utf-8"))
        felder = [{k: f[k] for k in ("nr", "titel", "jahr", "stunden")} for f in lf["lernfelder"] if titel_ok(f["titel"])]
        rows.append({"id": f"kmk:{e['id']}", "titel": e["titel"], "datum": e["datum"], "url": e["url"],
                     "datei": e["datei"], "lernfelder": felder, "befund": lf["befund"]})
    _write_jsonl(ROH_PATH, rows)
    st = Counter(r["befund"]["status"] for r in rows)
    return fertig(f"{len(rows)} Pläne eingelesen ({dict(st)}), {len(fehlt)} der KMK-Liste ohne Markdown-Fassung")


def lade_roh() -> list[dict]:
    if not ROH_PATH.exists():
        raise FileNotFoundError(f"{ROH_PATH.name} fehlt: erst den Schritt „lernfelder“ ausführen.")
    return load_jsonl(ROH_PATH)


def vollstaendig(plan: dict) -> bool:
    """Lernfeldliste ohne Lücken und mit Titel, Jahr und Zeitrichtwert je Lernfeld."""
    b = plan["befund"]
    return bool(plan["lernfelder"]) and not (b["luecken"] or b["ohne_titel"] or b["ohne_stunden"] or b["ohne_jahr"])


# --- 2. Kandidaten (Embeddings) --------------------------------------------------------------------

def _berufe() -> list[dict]:
    return load_jsonl(DIR / "berufe.jsonl")


def _plan_text(p: dict) -> str:
    return f"Ausbildungsberuf {p['titel']}. Lernfelder: " + "; ".join(f["titel"] for f in p["lernfelder"][:6])


def build_rlp_kandidaten(cfg: LLMConfig | None = None, embedding_modell: str | None = None,
                         embedder: Embedder | None = None, **_):
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    plaene = [p for p in lade_roh() if p["lernfelder"]]
    berufe = _berufe()
    bv = emb.encode([". ".join([b["label"], ", ".join(b.get("alt_labels", [])[:5]), b["beschreibung"][:300]])
                     for b in berufe], art="ziel")
    idx, sims = top_k(emb.encode([_plan_text(p) for p in plaene], art="anfrage"), bv, N_KANDIDATEN)
    out = {p["id"]: {_code(berufe[i]["uri"]): float(s) for i, s in zip(ri, rs)} for p, ri, rs in zip(plaene, idx, sims)}
    KANDIDATEN_PATH.write_text(json.dumps({"embedding_modell": emb.model, "plaene": out}, ensure_ascii=False),
                               encoding="utf-8")
    return fertig(f"{len(out)} Rahmenlehrpläne, Modell {emb.model}")


def lade_kandidaten() -> dict:
    if not KANDIDATEN_PATH.exists():
        raise FileNotFoundError(f"{KANDIDATEN_PATH.name} fehlt: erst den Schritt „rlp_kandidaten“ ausführen.")
    return json.loads(KANDIDATEN_PATH.read_text(encoding="utf-8"))


# --- 3. ESCO-Berufe abstimmen ----------------------------------------------------------------------

REGELN = """Zu jedem deutschen Ausbildungsberuf (Titel des KMK-Rahmenlehrplans, dazu die ersten Lernfelder) gibt es eine Liste von ESCO-Berufen (Kürzel | Beruf | KldB-Berufsuntergruppe).
- Wähle 1 bis 3 ESCO-Berufe, deren Tätigkeit dem Ausbildungsberuf entspricht, den passendsten zuerst. Gib als "code" das Kürzel an.
- Passend ist ein ESCO-Beruf, wenn eine Fachkraft mit diesem Ausbildungsabschluss typischerweise genau diese Tätigkeit ausübt; Fachrichtungen und Schwerpunkte des Ausbildungsberufs dürfen je einen eigenen ESCO-Beruf haben.
- Nicht passend: Helfer-, Meister-, Techniker- oder Studienberufe, Führungskräfte und bloß verwandte Berufe derselben Branche.
- Lieber ein Beruf zu wenig als einer zu viel. Passt keiner, gib eine leere Auswahl.
- Je Beruf: "begruendung" (ein kurzer Satz) und "belegbegriffe": 1 bis 3 Begriffe aus Titel oder Lernfeldern."""

PERSPEKTIVEN = [
    "Du bist Berufsberaterin der Bundesagentur für Arbeit und kennst die anerkannten Ausbildungsberufe.",
    "Du ordnest deutsche Ausbildungsberufe der europäischen Berufsklassifikation ESCO zu.",
    "Du bist Lehrkraft an einer Berufsschule und kennst die Rahmenlehrpläne der KMK.",
]


def _ziele() -> dict[str, Ziel]:
    return {_code(b["uri"]): Ziel(_code(b["uri"]), b["uri"], b["label"],
                                  " / ".join(x["label"] for x in b["berufsuntergruppe"][:1]))
            for b in _berufe()}


def _objekte() -> list[dict]:
    return [{"id": p["id"], "prompt": {"ausbildungsberuf": p["titel"],
                                       "lernfelder": [f["titel"] for f in p["lernfelder"][:6]]},
             "text": _plan_text(p)} for p in lade_roh() if p["lernfelder"]]


def build_rlp_berufe(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = N_STIMMEN, **_):
    kand = lade_kandidaten()["plaene"]
    objekte = [o for o in _objekte() if o["id"] in kand]
    systeme = [PERSPEKTIVEN[i % len(PERSPEKTIVEN)] + "\n\n" + REGELN for i in range(stimmen)]
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]) for o in objekte}, _ziele(), systeme,
                                STIMMEN_PATH, cfg, max_auswahl=3, batch=8, on_progress=on_progress,
                                stop_event=stop_event)


# --- 4. Zusammenführen -----------------------------------------------------------------------------

METHODE = ("Lernfelder aus Übersichtstabelle und Lernfeld-Kopfzeilen der Markdown-Fassung gelesen und abgeglichen; "
           "ESCO-Berufe per Embedding-Kandidaten und LLM-Mehrfachabstimmung")


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    st = abstimmung.lade_stimmen(STIMMEN_PATH)
    kand_all = lade_kandidaten() if KANDIDATEN_PATH.exists() else {"plaene": {}}
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH)
    by_code = {_code(b["uri"]): b for b in _berufe()}
    meta = {"methode": METHODE, "modell": modell or (cfg.model if cfg else None),
            "embedding_modell": kand_all.get("embedding_modell"), "stimmen": stimmen, "datum": date.today().isoformat()}
    rows, pruef = [], []
    for p in lade_roh():
        if not p["lernfelder"]:
            continue
        a = abstimmung.auswerten(st.get(p["id"], {}), stimmen, p["titel"], max_auswahl=3,
                                 aehnlichkeit=kand_all["plaene"].get(p["id"], {}))
        codes, status = [c for c in a["auswahl"] if c in by_code], a["status"]
        if not st.get(p["id"]):
            codes, status = [], "unsicher"
        k = korr.get(p["id"])
        if k:
            if k["ausschliessen"]:
                continue
            codes, status = [c for c in k["codes"] or codes if c in by_code], "redaktionell"
        berufe = [{"uri": by_code[c]["uri"], "label": by_code[c]["label"], "code": c,
                   **{x: d[x] for d in a["details"] if d["code"] == c for x in ("stimmen", "konfidenz")}}
                  for c in codes]
        row = {**{x: p[x] for x in ("id", "titel", "datum", "url", "datei", "lernfelder")},
               "vollstaendig": vollstaendig(p), "berufe": berufe,
               "quelle": {k2: rlp.QUELLE[k2] for k2 in ("name", "uri", "lizenz", "quellenangabe")},
               "mapping": {**meta, "status": status, "verworfen": a["verworfen"],
                           **({"korrektur": k} if k else {})}}
        rows.append(row)
        gruende = []
        if status == "unsicher":
            gruende.append("keine Mehrheit für einen ESCO-Beruf" if st.get(p["id"]) else "noch nicht abgestimmt")
        if not codes and status != "unsicher":
            gruende.append("kein passender ESCO-Beruf")
        if not row["vollstaendig"]:
            gruende.append(f"Lernfeldliste lückenhaft ({p['befund']['status']})")
        if gruende and not k:
            pruef.append({"id": p["id"], "titel": p["titel"], "grund": "; ".join(gruende),
                          "esco": " | ".join(f"{b['code']} {b['label']}" for b in berufe),
                          "kandidaten": " | ".join(f"{c} {by_code[c]['label']}"
                                                   for c in list(kand_all["plaene"].get(p["id"], {}))[:6]
                                                   if c in by_code),
                          "lernfelder": len(p["lernfelder"]), "url": p["url"]})
    _write_jsonl(lokal_pfad(PLAENE), rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "kommentar"])
    mit = sum(1 for r in rows if r["berufe"])
    return fertig(f"{len(rows)} Rahmenlehrpläne, {mit} mit ESCO-Beruf, "
                  f"{sum(len(r['lernfelder']) for r in rows if r['berufe'])} Lernfelder nutzbar, "
                  f"{len(pruef)} auf der Prüfliste")


def lade_plaene() -> list[dict]:
    path = lokal_pfad(PLAENE)
    if not path.exists():
        warnings.warn(f"{path} fehlt: keine Lernfeld-Themen in der Berufsbildung (siehe data/README.md, data/lokal/)")
        return []
    return load_jsonl(path)


def statistik(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else lade_plaene()
    return {"rahmenlehrplaene": len(rows), "mit_esco_beruf": sum(1 for r in rows if r["berufe"]),
            "vollstaendig": sum(1 for r in rows if r["vollstaendig"]),
            "lernfelder": sum(len(r["lernfelder"]) for r in rows),
            "status": dict(Counter(r["mapping"]["status"] for r in rows))}


def lernfeld_text(lf: dict) -> str:
    """„Lernfeld 5: Titel (2. Ausbildungsjahr, 80 Stunden)“ für Prompt und Vorschau."""
    extra = ", ".join(x for x in [f"{lf['jahr']}. Ausbildungsjahr" if lf.get("jahr") else "",
                                  f"{lf['stunden']} Stunden" if lf.get("stunden") else ""] if x)
    nr = re.sub(r"(\d)([a-z]+)$", r"\1 \2", lf["nr"])
    return f"Lernfeld {nr}: {lf['titel']}" + (f" ({extra})" if extra else "")
