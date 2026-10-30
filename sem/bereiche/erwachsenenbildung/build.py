"""Baut die Mappings der Erwachsenenbildung unter data/mappings/erwachsenenbildung/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  kandidaten_llm   je Fachgebiet der Volkshochschul-Statistik typische Kursthemen aus zwei Perspektiven (LLM);
                   die Fremdsprachen sind dabei ein gemeinsames Ziel „4.fs“ (Kursthemen gelten für alle Sprachen)
  gnd              alle Kandidaten exakt auf GND-Sachschlagwörter abgleichen (lobid), nur Sachbegriffe
  wlo              Keywords der WLO-Datensätze mit Bildungsstufe Erwachsenenbildung, nur als Beleg (Export bleibt lokal)
  kandidaten       je Begriff die ähnlichsten Fachgebiete (lokale Embeddings) plus die Fachgebiete der LLM-Kandidatenliste
  eignung          drei Stimmen: Kursthema für Teilnehmende, Thema für Kursleitende oder ungeeignet
  fachgebiete      drei Stimmen: ein bis zwei Fachgebiete je Thema
  zusammenfuehren  themen_erwachsenenbildung.jsonl, pruefliste_themen.csv; korrekturen.csv hat Vorrang
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ...llm import LLMClient, LLMConfig, Progress, run_tasks
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder, top_k
from ...paths import DATA_DIR
from ...quellen import gnd
from ..base import fertig
from . import DIR, FREMDSPRACHEN, ZIEL_FS, load_jsonl, systematik

CACHE = DATA_DIR / "cache"
THEMEN_PATH = DIR / "themen_erwachsenenbildung.jsonl"
PRUEFLISTE_PATH = DIR / "pruefliste_themen.csv"
KORREKTUREN_PATH = DIR / "korrekturen.csv"
KANDIDATEN_LLM = CACHE / "eb_kandidaten_llm.jsonl"
POOL_PATH = CACHE / "eb_gnd_pool.jsonl"
WLO_PATH = CACHE / "wlo_keywords_erwachsenenbildung.json"
KANDIDATEN_PATH = CACHE / "eb_kandidaten.json"
STIMMEN_EIGNUNG = CACHE / "eb_stimmen_eignung.jsonl"
STIMMEN_FG = CACHE / "eb_stimmen_fachgebiete.jsonl"
ARTEN = ["kursthema", "kursleitungsthema"]
N_STIMMEN = 3
N_KANDIDATEN = 6
WLO_MIN_NODES = 2


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def ziele() -> dict[str, dict]:
    """Zuordnungsziele: alle Fachgebiete ohne Sammelpositionen; Fremdsprachen als ein Ziel 4.fs."""
    s = systematik()
    pb = {p["notation"]: p["label"] for p in s["programmbereiche"]}
    out = {}
    for f in s["fachgebiete"]:
        if f["uebergreifend"] or f["notation"] in FREMDSPRACHEN:
            continue
        out[f["notation"]] = {"notation": f["notation"], "label": f["label"], "programmbereich": pb[f["programmbereich"]]}
    out[ZIEL_FS] = {"notation": ZIEL_FS, "label": "Fremdsprachen (Kursthemen für alle Sprachen)", "programmbereich": pb["4"]}
    return dict(sorted(out.items()))


# --- 1. Kandidaten aus dem LLM ---------------------------------------------------------------------

KANDIDATEN_SYSTEM = """Du sammelst Themen für Kurse an Volkshochschulen in Deutschland (Erwachsenenbildung).

Antworte ausschließlich mit JSON: {"themen": ["…"]}
- 30 bis 40 Themen für das genannte Fachgebiet, je ein kurzer deutscher Begriff, wie er als Schlagwort in einem Bibliothekskatalog stehen könnte (Substantiv oder etablierter Fachbegriff, z. B. "Stressbewältigung", "Tabellenkalkulation", "Aquarellmalerei", "Mietrecht").
- Bei Sprachkursen: Kommunikationssituationen und Themen des Kurses (z. B. "Arztbesuch", "Wohnungssuche", "Bewerbung"), keine Grammatikbegriffe.
- Etwa ein Zehntel darf Themen für Kursleitende betreffen (Didaktik der Erwachsenenbildung).
- Keine Sätze, keine Kursnamen, keine Eigennamen."""

KANDIDATEN_PERSPEKTIVEN = [
    "Du bist Fachbereichsleiterin an einer Volkshochschule und planst das Semesterprogramm.",
    "Du bist Kursleiter in der Erwachsenenbildung und kennst die Wünsche der Teilnehmenden.",
]


def build_kandidaten_llm(cfg: LLMConfig, on_progress=None, stop_event=None, **_):
    tasks = [{"id": f"{z['notation']}:{i}", "ziel": z, "perspektive": p}
             for z in ziele().values() for i, p in enumerate(KANDIDATEN_PERSPEKTIVEN)]

    def worker(client: LLMClient, task: dict) -> dict:
        z = task["ziel"]
        res = client.chat_json(task["perspektive"] + "\n\n" + KANDIDATEN_SYSTEM,
                               f"Programmbereich: {z['programmbereich']}\nFachgebiet: {z['label']}")
        themen = [str(t).strip() for t in res.get("themen", []) if 2 < len(str(t).strip()) <= 60]
        if len(themen) < 15:
            raise ValueError(f"nur {len(themen)} Themen")
        return {"id": task["id"], "fachgebiet": z["notation"], "themen": themen}

    return run_tasks(tasks, worker, KANDIDATEN_LLM, cfg, on_progress=on_progress, stop_event=stop_event)


# --- 2. GND-Abgleich -------------------------------------------------------------------------------

def _kandidaten_llm() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in load_jsonl(KANDIDATEN_LLM) if KANDIDATEN_LLM.exists() else []:
        for t in r["themen"]:
            e = out.setdefault(_norm(t), {"label": t, "fachgebiete": Counter()})
            e["fachgebiete"][r["fachgebiet"]] += 1
    return out


def build_gnd(cfg: LLMConfig | None = None, on_progress=None, workers: int = 6, **_):
    kand = _kandidaten_llm()
    if not kand:
        raise FileNotFoundError("Keine Kandidaten: erst „kandidaten_llm“ ausführen.")
    prog = Progress(total=len(kand), status="GND-Abgleich")
    pool: dict[str, dict] = {}

    def suche(item):
        return item, gnd.suche_exakt(item[1]["label"])

    with ThreadPoolExecutor(workers) as ex:
        for (_, k), rec in ex.map(suche, kand.items()):
            prog.done += 1
            if on_progress and prog.done % 200 == 0:
                on_progress(prog)
            if not rec or not gnd.ist_thema(rec):
                continue
            e = pool.setdefault(rec["uri"], {**rec, "kandidaten": [], "llm_fachgebiete": Counter()})
            e["kandidaten"].append(k["label"])
            e["llm_fachgebiete"].update(k["fachgebiete"])
    rows = [{**v, "llm_fachgebiete": dict(v["llm_fachgebiete"])} for v in pool.values()]
    rows.sort(key=lambda r: r["label"].casefold())
    _write_jsonl(POOL_PATH, rows)
    return fertig(f"{len(kand)} Kandidaten, {len(rows)} GND-Sachbegriffe")


def lade_pool() -> list[dict]:
    if not POOL_PATH.exists():
        raise FileNotFoundError(f"{POOL_PATH.name} fehlt: erst den Schritt „gnd“ ausführen.")
    return load_jsonl(POOL_PATH)


# --- 3. WLO (nur Beleg) ----------------------------------------------------------------------------

WLO_QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
              "hinweis": "nur Keywords von Datensätzen der Bildungsstufe Erwachsenenbildung, ausschließlich als Beleg "
                         "(Zahl der Datensätze, Node-IDs)"}


def build_wlo(cfg: LLMConfig | None = None, wlo_dateien: list[str] | None = None, **_):
    from ...quellen.wlo import _liste

    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    csv.field_size_limit(10 ** 9)
    data: dict = defaultdict(set)
    for path in wlo_dateien:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=";")

            def spalte(name):
                return next((c for c in r.fieldnames if c == name or c.endswith("." + name)), None)

            c_node, c_ctx, c_kw = spalte("sys:node-uuid"), spalte("ccm:educationalcontext_DISPLAYNAME"), \
                spalte("cclom:general_keyword")
            for i, row in enumerate(r):
                if "Erwachsenenbildung" not in _liste(row, c_ctx):
                    continue
                node = row[c_node].strip() if c_node else f"#{Path(path).name}:{i}"
                for kw in {k.strip() for k in (row.get(c_kw) or "").split(",") if k.strip()}:
                    data[_norm(kw)].add(node)
    rows = {k: {"anzahl": len(v), "nodes": sorted(n for n in v if not n.startswith("#"))[:10]}
            for k, v in data.items() if len(v) >= WLO_MIN_NODES}
    WLO_PATH.parent.mkdir(parents=True, exist_ok=True)
    WLO_PATH.write_text(json.dumps({"quelle": WLO_QUELLE, "keywords": rows}, ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(rows)} Keywords aus der Erwachsenenbildung (nur Beleg)")


def lade_wlo() -> dict:
    return json.loads(WLO_PATH.read_text(encoding="utf-8"))["keywords"] if WLO_PATH.exists() else {}


# --- 4. Kandidaten (Embeddings) --------------------------------------------------------------------

def _objekte(limit: int | None = None) -> list[dict]:
    out = []
    for r in lade_pool()[:limit]:
        prompt = {"begriff": r["label"], "synonyme": r["synonyme"][:5],
                  "oberbegriffe": [o["label"] for o in r["oberbegriffe"]][:3],
                  "gnd_sachgruppe": [s["label"] for s in r["sachgruppen"]][:2]}
        if r.get("definition"):
            prompt["definition"] = r["definition"][:300]
        text = " ".join([r["label"], " ".join(r["synonyme"]), " ".join(prompt["oberbegriffe"]), r.get("definition", "")])
        out.append({"id": r["uri"], "pool": r, "prompt": prompt, "text": text})
    return out


def build_kandidaten(cfg: LLMConfig | None = None, embedding_modell: str | None = None, embedder: Embedder | None = None,
                     **_):
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    z = list(ziele().values())
    zv = emb.encode([f"Volkshochschulkurs im Fachgebiet {x['label']}, Programmbereich {x['programmbereich']}" for x in z],
                    art="ziel")
    objekte = _objekte()
    idx, sims = top_k(emb.encode([o["text"] for o in objekte], art="anfrage"), zv, N_KANDIDATEN)
    out = {}
    for o, ri, rs in zip(objekte, idx, sims):
        wahl = {z[i]["notation"]: float(s) for i, s in zip(ri, rs)}
        for fg in o["pool"]["llm_fachgebiete"]:
            wahl.setdefault(fg, 0.0)
        out[o["id"]] = wahl
    KANDIDATEN_PATH.write_text(json.dumps({"embedding_modell": emb.model, "themen": out}, ensure_ascii=False),
                               encoding="utf-8")
    return fertig(f"{len(out)} Begriffe, Modell {emb.model}")


def lade_kandidaten() -> dict:
    if not KANDIDATEN_PATH.exists():
        raise FileNotFoundError(f"{KANDIDATEN_PATH.name} fehlt: erst den Schritt „kandidaten“ ausführen.")
    return json.loads(KANDIDATEN_PATH.read_text(encoding="utf-8"))


def _systeme(perspektiven: list[str], regeln: str, n: int) -> list[str]:
    return [perspektiven[i % len(perspektiven)] + "\n\n" + regeln for i in range(n)]


PERSPEKTIVEN = [
    "Du bist Fachbereichsleiterin an einer Volkshochschule.",
    "Du bist Programmplaner in der Erwachsenenbildung und kennst die Volkshochschul-Statistik.",
    "Du bist Kursleiterin mit langjähriger Erfahrung in der Erwachsenenbildung.",
]

# --- 5. Eignung ------------------------------------------------------------------------------------

EIGNUNG_ZIELE = {
    "kursthema": Ziel("kursthema", "kursthema", "Kursthema für Teilnehmende",
                      "Erwachsene lernen dazu etwas in einem Kurs, Vortrag oder Selbstlernangebot"),
    "kursleitungsthema": Ziel("kursleitungsthema", "kursleitungsthema", "Thema für Kursleitende",
                              "Didaktik, Methoden und Organisation der Erwachsenenbildung"),
    "ungeeignet": Ziel("ungeeignet", "ungeeignet", "ungeeignet",
                       "zu speziell, zu abstrakt oder ohne sinnvolles Kursangebot für Erwachsene"),
}

EIGNUNG_REGELN = """Entscheide für jeden GND-Begriff genau eine Kategorie (code aus den Kandidaten).
- "kursthema": Zu diesem Begriff gibt es typischerweise Kurse, Vorträge oder Selbstlernangebote für Erwachsene (Volkshochschule, Weiterbildung).
- "kursleitungsthema": ein Thema der Didaktik, Methodik oder Organisation der Erwachsenenbildung.
- "ungeeignet": alles andere, auch reine Ordnungsbegriffe und Begriffe ohne Lehrangebot.
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus Begriff, Synonymen oder Oberbegriffen an."""


def build_eignung(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                  stimmen: int = N_STIMMEN, **_):
    objekte = _objekte(limit)
    alle = list(EIGNUNG_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, EIGNUNG_ZIELE,
                                _systeme(PERSPEKTIVEN, EIGNUNG_REGELN, stimmen), STIMMEN_EIGNUNG, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 6. Fachgebiete --------------------------------------------------------------------------------

FG_REGELN = """Ordne jedes Thema ein bis zwei Fachgebieten der Volkshochschul-Statistik zu (code aus den Kandidaten), das passendste zuerst.
- Entscheidend ist, in welchem Fachgebiet eine Volkshochschule einen Kurs zu diesem Thema anbieten würde.
- "4.fs" steht für alle Fremdsprachenkurse: wähle es für Kommunikationssituationen und Alltagsthemen, die typischerweise im Fremdsprachenunterricht vorkommen, nicht für das Fachthema selbst.
- Lieber ein Fachgebiet zu wenig als eins zu viel.
Je Fachgebiet: "begruendung" (ein kurzer Satz) und 1 bis 3 "belegbegriffe" aus Begriff, Synonymen oder Oberbegriffen."""


def _geeignet(limit: int | None = None) -> list[dict]:
    st = abstimmung.lade_stimmen(STIMMEN_EIGNUNG)
    out = []
    for o in _objekte(limit):
        a = abstimmung.auswerten(st.get(o["id"], {}), N_STIMMEN, max_auswahl=1)
        if a["auswahl"] and a["auswahl"][0] != "ungeeignet":
            out.append(o)
    return out


def build_fachgebiete(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                      stimmen: int = N_STIMMEN, **_):
    kand = lade_kandidaten()["themen"]
    objekte = [o for o in _geeignet(limit) if o["id"] in kand]
    zz = {k: Ziel(k, k, v["label"], "Programmbereich " + v["programmbereich"]) for k, v in ziele().items()}
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]) for o in objekte}, zz,
                                _systeme(PERSPEKTIVEN, FG_REGELN, stimmen), STIMMEN_FG, cfg,
                                max_auswahl=2, batch=15, on_progress=on_progress, stop_event=stop_event)


# --- 7. Zusammenführen -----------------------------------------------------------------------------

METHODE = ("Kandidaten aus LLM-Themenlisten je Fachgebiet der Volkshochschul-Statistik, exakt auf GND-Sachschlagwörter "
           "abgeglichen; Fachgebiet-Kandidaten per Embedding und Herkunftsliste; LLM-Mehrfachabstimmung zu Eignung "
           "und Fachgebiet")


def bewerte(tid, text, se, sf, korr, stimmen, sims, llm_fg, meta):
    ea = abstimmung.auswerten(se, stimmen, text, max_auswahl=1)
    fa = abstimmung.auswerten(sf, stimmen, text, max_auswahl=2, aehnlichkeit=sims)
    art = ea["auswahl"][0] if ea["auswahl"] else None
    codes = fa["auswahl"]
    zl = ziele()
    mapping = {**meta, "status": fa["status"], "konfidenz": fa["details"][0]["konfidenz"] if fa["details"] else 0.0,
               "llm_kandidat_bestaetigt": bool(set(codes) & set(llm_fg)), "kandidaten": len(sims),
               "fachgebiete": [{"notation": d["code"], "label": zl[d["code"]]["label"],
                                **{k: v for k, v in d.items() if k != "code"}} for d in fa["details"]],
               "verworfen": {zl[c]["label"]: n for c, n in fa["verworfen"].items() if c in zl},
               "eignung": {"status": ea["status"], "art": art,
                           "stimmen": {**{z: 0 for z in EIGNUNG_ZIELE}, **{d["code"]: d["stimmen"] for d in ea["details"]},
                                       **ea["verworfen"]},
                           "begruendungen": ea["details"][0]["begruendungen"] if ea["details"] else []}}
    if not codes and llm_fg and art not in (None, "ungeeignet"):
        codes = [max(llm_fg, key=llm_fg.get)]
        mapping.update({"status": "unsicher", "konfidenz": 0.0, "ersatz": "kein Fachgebiet gewählt; Herkunftsliste"})
    k = korr.get(tid)
    if k:
        if k["ausschliessen"]:
            return None, [], mapping, []
        if k["codes"]:
            codes = [c for c in k["codes"] if c in zl][:2] or codes
            art = next((c for c in k["codes"] if c in ARTEN), art)
            mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
            mapping["eignung"]["status"] = "redaktionell"
        if art in (None, "ungeeignet"):
            art = "kursthema"
        mapping["korrektur"] = k
        return art, codes, mapping, []
    gruende = []
    if ea["status"] == "unsicher":
        gruende.append("keine Mehrheit für die Eignung")
    if mapping.get("ersatz"):
        gruende.append("keine Stimme für ein Fachgebiet")
    elif fa["status"] == "unsicher" and art not in (None, "ungeeignet"):
        gruende.append("keine Mehrheit für ein Fachgebiet")
    return art, codes, mapping, gruende


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    se_all, sf_all = abstimmung.lade_stimmen(STIMMEN_EIGNUNG), abstimmung.lade_stimmen(STIMMEN_FG)
    kand_all = lade_kandidaten()
    kand = kand_all["themen"]
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH, ebene_spalte="korrektur_art")
    wlo = lade_wlo()
    zl = ziele()
    s = systematik()
    meta = {"methode": METHODE, "modell": modell or (cfg.model if cfg else None),
            "embedding_modell": kand_all.get("embedding_modell"), "stimmen": stimmen, "datum": date.today().isoformat(),
            "prompt_hash": _hash(KANDIDATEN_SYSTEM + EIGNUNG_REGELN + FG_REGELN + "".join(PERSPEKTIVEN))}
    rows, pruef = [], []
    ungeeignet = 0
    for o in _objekte():
        tid, p = o["id"], o["pool"]
        if tid not in se_all:
            continue
        art, codes, mapping, gruende = bewerte(tid, o["text"], se_all[tid], sf_all.get(tid, {}), korr, stimmen,
                                               kand.get(tid, {}), p["llm_fachgebiete"], meta)
        if art in (None, "ungeeignet") and mapping["eignung"]["status"] != "unsicher" and "korrektur" not in mapping:
            ungeeignet += 1
            continue
        if not codes:
            continue
        pbs = list(dict.fromkeys("4" if c == "4.fs" else c[0] for c in codes))
        row = {"id": tid, "label_de": re.sub(r"\s*<[^>]*>", "", p["label"]).strip(), "label_gnd": p["label"],
               "synonyme": p["synonyme"], "definition": p.get("definition", ""), "oberbegriffe": p["oberbegriffe"],
               "gnd_sachgruppen": p["sachgruppen"],
               "art": art if art in ARTEN else "kursthema", "herkunft": "gnd",
               "fachgebiet": [{"notation": c, "uri": s["uri_basis"] + "fg" + c, "label": zl[c]["label"]} for c in codes],
               "programmbereich": [{"notation": n, "uri": s["uri_basis"] + "pb" + n,
                                    "label": next(x["label"] for x in s["programmbereiche"] if x["notation"] == n)}
                                   for n in pbs],
               "quelle": {"name": "Gemeinsame Normdatei (GND)", "uri": tid, "lizenz": "CC0-1.0"}, "mapping": mapping}
        w = [wlo[_norm(x)] for x in [p["label"], *p.get("kandidaten", []), *p["synonyme"]] if _norm(x) in wlo]
        if w:
            row["belege"] = {"wlo": {"quelle": WLO_QUELLE, "datensaetze": max(e["anzahl"] for e in w),
                                     "nodes": sorted({n for e in w for n in e["nodes"]})[:10]}}
        rows.append(row)
        if gruende:
            pruef.append({"id": tid, "label_de": p["label"], "grund": "; ".join(gruende),
                          "art": f"{row['art']} ({mapping['eignung']['stimmen']})",
                          "fachgebiete": " | ".join(f"{f['notation']} {f['label']} ({f['stimmen']}/{stimmen})"
                                                    for f in mapping["fachgebiete"]) or " | ".join(codes) + " (Ersatz)",
                          "herkunft": " | ".join(p["llm_fachgebiete"])})
    rows += sprachkurs_themen(s) + schulabschluss_themen(s)
    rows.sort(key=lambda r: (r["fachgebiet"][0]["notation"], r["label_de"].casefold()))
    _write_jsonl(THEMEN_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "korrektur_art", "kommentar"])
    return fertig(f"{len(rows)} Themen, {ungeeignet} als ungeeignet verworfen, {len(pruef)} auf der Prüfliste")


def _fg_ref(s: dict, codes: list[str]) -> tuple[list[dict], list[dict]]:
    zl = ziele()
    fg = [{"notation": c, "uri": s["uri_basis"] + "fg" + c, "label": zl[c]["label"]} for c in codes]
    pbs = list(dict.fromkeys("4" if c == ZIEL_FS else c[0] for c in codes))
    pb = [{"notation": n, "uri": s["uri_basis"] + "pb" + n,
           "label": next(x["label"] for x in s["programmbereiche"] if x["notation"] == n)} for n in pbs]
    return fg, pb


def sprachkurs_themen(s: dict) -> list[dict]:
    """Sprachkurs-Themen aus der Gliederung des Rahmencurriculums für Integrationskurse (sprachkurs_themen.json):
    GND-Begriffe treffen Kommunikationssituationen wie „Formulare ausfüllen“ kaum."""
    path = DIR / "sprachkurs_themen.json"
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    q = d["quelle"]
    out = []
    for t in d["themen"]:
        fg, pb = _fg_ref(s, t["fachgebiete"])
        out.append({"id": f"{q['uri']}#{t['code']}", "label_de": t["label"], "handlungsfeld": t["handlungsfeld"],
                    "art": "kursthema", "fachgebiet": fg, "programmbereich": pb, "herkunft": "rahmencurriculum",
                    "quelle": {k: q[k] for k in ("name", "uri", "lizenz", "quellenangabe") if k in q},
                    "mapping": {"methode": "aus der Gliederung des Rahmencurriculums übernommen (Handlungsfeld und "
                                          "Lernzielbereich)", "status": "gesichert",
                                "eignung": {"status": "gesichert", "art": "kursthema"}}})
    return out


SCHULABSCHLUSS_PORTALE = {"Deutsch", "Mathematik", "Englisch"}


def schulabschluss_themen(s: dict) -> list[dict]:
    """Themen für Kurse zum Nachholen von Schulabschlüssen: Unterrichtsthemen des Bereichs Schule in den
    Prüfungsfächern Deutsch, Mathematik und Englisch (Sek I → Haupt-/Realschulabschluss, Sek II → FHR/Abitur)."""
    from ..schule import DIR as SCHULE_DIR

    path = SCHULE_DIR / "themen_schule.jsonl"
    if not path.exists():
        return []
    out = []
    for t in load_jsonl(path):
        if t["portal"] not in SCHULABSCHLUSS_PORTALE or t["art"] != "unterrichtsthema"                 or t.get("herkunft", "themenbaum") != "themenbaum":
            continue
        codes = (["6.02", "6.01"] if "sek1" in t["stufen"] else []) + (["6.04", "6.03"] if "sek2" in t["stufen"] else [])
        if not codes:
            continue
        codes = codes[:1] + codes[2:3] if len(codes) == 4 else codes
        fg, pb = _fg_ref(s, codes)
        m = t.get("mapping") or {}
        out.append({"id": t["id"], "label_de": t["label_de"], "definition": t.get("definition", ""),
                    "synonyme": [], "keywords": t.get("keywords", [])[:6], "schulfach": t["portal"],
                    "art": "kursthema", "fachgebiet": fg, "programmbereich": pb, "herkunft": "schulthemen",
                    "quelle": t["quelle"],
                    "mapping": {"methode": "Unterrichtsthema des Bereichs Schule im Prüfungsfach, Schulstufe → "
                                          "Abschluss", "status": m.get("status", "gesichert"),
                                "eignung": {"status": (m.get("art") or {}).get("status", "gesichert"), "art": "kursthema"}}})
    return out


def statistik(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else (load_jsonl(THEMEN_PATH) if THEMEN_PATH.exists() else [])
    m = [r.get("mapping", {}) for r in rows]
    return {
        "themen": len(rows),
        "art": dict(Counter(r["art"] for r in rows)),
        "programmbereich": dict(sorted(Counter(r["programmbereich"][0]["notation"] for r in rows).items())),
        "herkunft": dict(Counter(r.get("herkunft", "gnd") for r in rows)),
        "fachgebiete_belegt": len({f["notation"] for r in rows for f in r["fachgebiet"]}),
        "status_fachgebiet": dict(Counter(x.get("status") for x in m)),
        "status_eignung": dict(Counter((x.get("eignung") or {}).get("status") for x in m)),
        "llm_kandidat_bestaetigt": sum(1 for x in m if x.get("llm_kandidat_bestaetigt")),
        "mit_wlo_beleg": sum(1 for r in rows if r.get("belege")),
    }
