"""Baut die Elementar-Mappings unter data/mappings/elementar/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  kandidaten_llm   je Bildungsbereich typische Themen für Kitas aus drei Perspektiven (LLM)
  wlo              Keywords der WLO-Datensätze mit Bildungsstufe Elementarbereich (Export bleibt lokal)
  gnd              alle Kandidaten exakt auf GND-Sachschlagwörter abgleichen (lobid), nur Sachbegriffe
  eignung          drei Stimmen: Thema für Angebote mit Kindern, Thema für Fachkräfte/Eltern oder ungeeignet
  bereiche         drei Stimmen: ein bis zwei Bildungsbereiche je Thema
  alter            drei Stimmen: unter 3, ab 3 oder beide Altersgruppen
  zusammenfuehren  themen_elementar.jsonl, pruefliste_themen.csv; korrekturen.csv hat Vorrang
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
from ...paths import DATA_DIR
from ...quellen import gnd
from ..base import fertig
from . import DIR, bildungsbereiche, load_jsonl

CACHE = DATA_DIR / "cache"
THEMEN_PATH = DIR / "themen_elementar.jsonl"
PRUEFLISTE_PATH = DIR / "pruefliste_themen.csv"
KORREKTUREN_PATH = DIR / "korrekturen.csv"
KANDIDATEN_LLM = CACHE / "elementar_kandidaten_llm.jsonl"
WLO_PATH = CACHE / "wlo_keywords_elementar.json"
POOL_PATH = CACHE / "elementar_gnd_pool.jsonl"
STIMMEN_EIGNUNG = CACHE / "elementar_stimmen_eignung.jsonl"
STIMMEN_BEREICHE = CACHE / "elementar_stimmen_bereiche.jsonl"
STIMMEN_ALTER = CACHE / "elementar_stimmen_alter.jsonl"
ARTEN = ["kinderthema", "fachthema"]
ALTER = ["u3", "ue3", "beide"]
N_STIMMEN = 3
WLO_MIN_NODES = 2


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


# --- 1. Kandidaten aus dem LLM ---------------------------------------------------------------------

KANDIDATEN_SYSTEM = """Du sammelst Themen für Bildungsangebote in deutschen Kindertageseinrichtungen (Kinder von 0 bis 6 Jahren).

Antworte ausschließlich mit JSON: {"themen": ["…"]}
- 60 bis 80 Themen für den genannten Bildungsbereich, je ein kurzer deutscher Begriff, wie er als Schlagwort in einem Katalog stehen könnte (Substantiv im Singular oder etablierter Fachbegriff, z. B. "Jahreszeit", "Zahnpflege", "Regenwurm", "Eingewöhnung").
- Etwa drei Viertel Inhalte für Angebote mit Kindern, ein Viertel pädagogische Themen für Fachkräfte und Eltern.
- Keine Sätze, keine Aufzählungen von Methoden, keine Eigennamen."""

KANDIDATEN_PERSPEKTIVEN = [
    "Du bist erfahrene Erzieherin und planst das Kita-Jahr.",
    "Du bist Fachberater für Kindertageseinrichtungen und kennst die Bildungspläne der Länder.",
    "Du gestaltest Material für Eltern und Fachkräfte im Elementarbereich.",
]


def build_kandidaten_llm(cfg: LLMConfig, on_progress=None, stop_event=None, **_):
    tasks = [{"id": f"{b['id']}:{i}", "bereich": b, "perspektive": p}
             for b in bildungsbereiche()["bildungsbereiche"] for i, p in enumerate(KANDIDATEN_PERSPEKTIVEN)]

    def worker(client: LLMClient, task: dict) -> dict:
        b = task["bereich"]
        res = client.chat_json(task["perspektive"] + "\n\n" + KANDIDATEN_SYSTEM,
                               f"Bildungsbereich: {b['label']}\nWorum es geht: {b['beschreibung']}")
        themen = [str(t).strip() for t in res.get("themen", []) if 2 < len(str(t).strip()) <= 60]
        if len(themen) < 20:
            raise ValueError(f"nur {len(themen)} Themen")
        return {"id": task["id"], "bereich": b["id"], "themen": themen}

    return run_tasks(tasks, worker, KANDIDATEN_LLM, cfg, on_progress=on_progress, stop_event=stop_event)


# --- 2. WLO-Keywords -------------------------------------------------------------------------------

WLO_QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
              "hinweis": "nur Keywords von Datensätzen der Bildungsstufe Elementarbereich genutzt; Beleg als Zahl "
                         "der Datensätze und Node-IDs"}


def build_wlo(cfg: LLMConfig | None = None, wlo_dateien: list[str] | None = None, **_):
    from ...quellen.wlo import _liste

    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    csv.field_size_limit(10 ** 9)
    data: dict = defaultdict(lambda: {"nodes": set(), "schreibweisen": Counter()})
    for path in wlo_dateien:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=";")

            def spalte(name):
                return next((c for c in r.fieldnames if c == name or c.endswith("." + name)), None)

            c_node, c_ctx, c_kw = spalte("sys:node-uuid"), spalte("ccm:educationalcontext_DISPLAYNAME"), \
                spalte("cclom:general_keyword")
            for i, row in enumerate(r):
                if "Elementarbereich" not in _liste(row, c_ctx):
                    continue
                node = row[c_node].strip() if c_node else f"#{Path(path).name}:{i}"
                for kw in {k.strip() for k in (row.get(c_kw) or "").split(",") if k.strip()}:
                    e = data[_norm(kw)]
                    e["nodes"].add(node)
                    e["schreibweisen"][kw] += 1
    rows = {k: {"schreibweise": v["schreibweisen"].most_common(1)[0][0], "anzahl": len(v["nodes"]),
                "nodes": sorted(n for n in v["nodes"] if not n.startswith("#"))}
            for k, v in data.items() if len(v["nodes"]) >= WLO_MIN_NODES}
    WLO_PATH.parent.mkdir(parents=True, exist_ok=True)
    WLO_PATH.write_text(json.dumps({"quelle": WLO_QUELLE, "min_nodes": WLO_MIN_NODES, "keywords": rows},
                                   ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(rows)} Keywords aus dem Elementarbereich (mindestens {WLO_MIN_NODES} Datensätze)")


def lade_wlo() -> dict:
    return json.loads(WLO_PATH.read_text(encoding="utf-8"))["keywords"] if WLO_PATH.exists() else {}


# --- 3. GND-Abgleich -------------------------------------------------------------------------------

def _kandidaten() -> dict[str, dict]:
    """Normalisiertes Label → {"label", "llm_bereiche": Counter, "wlo": anzahl}."""
    out: dict[str, dict] = {}
    if KANDIDATEN_LLM.exists():
        for r in load_jsonl(KANDIDATEN_LLM):
            for t in r["themen"]:
                e = out.setdefault(_norm(t), {"label": t, "llm_bereiche": Counter(), "wlo": 0})
                e["llm_bereiche"][r["bereich"]] += 1
    for k, v in lade_wlo().items():
        e = out.setdefault(k, {"label": v["schreibweise"], "llm_bereiche": Counter(), "wlo": 0})
        e["wlo"] = v["anzahl"]
    return out


def build_gnd(cfg: LLMConfig | None = None, on_progress=None, workers: int = 6, **_):
    kand = _kandidaten()
    if not kand:
        raise FileNotFoundError("Keine Kandidaten: erst „kandidaten_llm“ und/oder „wlo“ ausführen.")
    prog = Progress(total=len(kand), status="GND-Abgleich")
    pool: dict[str, dict] = {}

    def suche(item):
        return item, gnd.suche_exakt(item[1]["label"])

    with ThreadPoolExecutor(workers) as ex:
        for (norm, k), rec in ex.map(suche, kand.items()):
            prog.done += 1
            if on_progress and prog.done % 100 == 0:
                on_progress(prog)
            if not rec or not gnd.ist_thema(rec):
                continue
            e = pool.setdefault(rec["uri"], {**rec, "kandidaten": [], "llm_bereiche": Counter(), "wlo": 0})
            e["kandidaten"].append(k["label"])
            e["llm_bereiche"].update(k["llm_bereiche"])
            e["wlo"] += k["wlo"]
    rows = [{**v, "llm_bereiche": dict(v["llm_bereiche"])} for v in pool.values()]
    rows.sort(key=lambda r: r["label"].casefold())
    _write_jsonl(POOL_PATH, rows)
    return fertig(f"{len(kand)} Kandidaten, {len(rows)} GND-Sachbegriffe")


def lade_pool() -> list[dict]:
    if not POOL_PATH.exists():
        raise FileNotFoundError(f"{POOL_PATH.name} fehlt: erst den Schritt „gnd“ ausführen.")
    return load_jsonl(POOL_PATH)


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


def _systeme(perspektiven: list[str], regeln: str, n: int) -> list[str]:
    return [perspektiven[i % len(perspektiven)] + "\n\n" + regeln for i in range(n)]


PERSPEKTIVEN = [
    "Du bist erfahrene Erzieherin in einer Kindertageseinrichtung.",
    "Du bist Fachberater für Kindertageseinrichtungen und kennst die Bildungspläne der Länder.",
    "Du bist Kindheitspädagogin und bildest Fachkräfte für den Elementarbereich aus.",
]

# --- 4. Eignung ----------------------------------------------------------------------------------

EIGNUNG_ZIELE = {
    "kinderthema": Ziel("kinderthema", "kinderthema", "Thema für Angebote mit Kindern",
                        "Kinder von 0 bis 6 können sich in der Kita damit beschäftigen (Spiel, Gespräch, Projekt, Lied)"),
    "fachthema": Ziel("fachthema", "fachthema", "Thema für Fachkräfte und Eltern",
                      "pädagogisches, entwicklungspsychologisches oder organisatorisches Thema der frühen Bildung"),
    "ungeeignet": Ziel("ungeeignet", "ungeeignet", "ungeeignet",
                       "zu speziell, zu abstrakt, nicht kindgerecht oder ohne Bezug zur frühen Bildung"),
}

EIGNUNG_REGELN = """Entscheide für jeden GND-Begriff genau eine Kategorie (code aus den Kandidaten).
- "kinderthema": ein Inhalt, mit dem Kinder in der Kita etwas erleben, erforschen, gestalten oder besprechen können (z. B. Regenwurm, Jahreszeit, Zahnpflege, Freundschaft, Feuerwehr).
- "fachthema": ein Thema, über das sich Fachkräfte oder Eltern informieren (z. B. Eingewöhnung, Sprachförderung, Beobachtung, Kinderschutz, Trotzphase).
- "ungeeignet": alles andere, auch Begriffe, die für Kinder belastend, gefährlich oder nicht altersgerecht sind, sowie Fachbegriffe ohne Bezug zur frühen Bildung.
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus Begriff, Synonymen oder Oberbegriffen an."""


def build_eignung(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                  stimmen: int = N_STIMMEN, **_):
    objekte = _objekte(limit)
    alle = list(EIGNUNG_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, EIGNUNG_ZIELE,
                                _systeme(PERSPEKTIVEN, EIGNUNG_REGELN, stimmen), STIMMEN_EIGNUNG, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


def _geeignet(limit: int | None = None) -> list[dict]:
    """Objekte, für die die Eignungsabstimmung keine Mehrheit für „ungeeignet“ ergibt."""
    st = abstimmung.lade_stimmen(STIMMEN_EIGNUNG)
    out = []
    for o in _objekte(limit):
        a = abstimmung.auswerten(st.get(o["id"], {}), N_STIMMEN, max_auswahl=1)
        if a["auswahl"] and a["auswahl"][0] != "ungeeignet":
            out.append(o)
    return out


# --- 5. Bildungsbereiche ---------------------------------------------------------------------------

def bereich_ziele() -> dict[str, Ziel]:
    return {b["id"]: Ziel(b["id"], b["id"], b["label"], b["beschreibung"]) for b in bildungsbereiche()["bildungsbereiche"]}


BEREICHE_REGELN = """Ordne jedes Thema ein bis zwei Bildungsbereichen des Gemeinsamen Rahmens der Länder zu (code aus den Kandidaten), den passendsten zuerst.
- Entscheidend ist, in welchem Bildungsbereich Fachkräfte das Thema in der Kita typischerweise aufgreifen.
- Lieber ein Bereich zu wenig als einer zu viel; ein zweiter Bereich nur, wenn das Thema dort ebenso typisch ist.
Je Bereich: "begruendung" (ein kurzer Satz) und 1 bis 3 "belegbegriffe" aus Begriff, Synonymen oder Oberbegriffen."""


def build_bereiche(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                   stimmen: int = N_STIMMEN, **_):
    objekte = _geeignet(limit)
    ziele = bereich_ziele()
    return abstimmung.abstimmen(objekte, {o["id"]: list(ziele) for o in objekte}, ziele,
                                _systeme(PERSPEKTIVEN, BEREICHE_REGELN, stimmen), STIMMEN_BEREICHE, cfg,
                                max_auswahl=2, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 6. Altersgruppe -------------------------------------------------------------------------------

ALTER_ZIELE = {
    "u3": Ziel("u3", "u3", "unter 3 Jahre", "vor allem für Krippenkinder (Sinneserfahrung, Pflege, erste Wörter, Bewegung)"),
    "ue3": Ziel("ue3", "ue3", "ab 3 Jahre", "vor allem für Kindergartenkinder bis zum Schuleintritt"),
    "beide": Ziel("beide", "beide", "beide Altersgruppen", "für Kinder unter und über 3 Jahren gleichermaßen passend"),
}

ALTER_REGELN = """Ordne jedes Thema genau einer Altersgruppe zu (code aus den Kandidaten): Für welche Kinder wird das Thema in der Kita typischerweise aufgegriffen?
Bei Themen für Fachkräfte und Eltern gilt die Altersgruppe der Kinder, um die es geht (z. B. Eingewöhnung in die Krippe: "u3").
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" an."""


def build_alter(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                stimmen: int = N_STIMMEN, **_):
    objekte = _geeignet(limit)
    alle = list(ALTER_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, ALTER_ZIELE,
                                _systeme(PERSPEKTIVEN, ALTER_REGELN, stimmen), STIMMEN_ALTER, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 7. Zusammenführen -----------------------------------------------------------------------------

METHODE = ("Kandidaten aus LLM-Themenlisten je Bildungsbereich und WLO-Keywords, exakt auf GND-Sachschlagwörter "
           "abgeglichen; dann LLM-Mehrfachabstimmung zu Eignung, Bildungsbereich und Altersgruppe")


def bewerte(tid: str, text: str, se: dict, sb: dict, sa: dict, korr: dict, stimmen: int, llm_bereiche: dict,
            meta: dict) -> tuple[str | None, list[str], str, dict, list[str]]:
    ea = abstimmung.auswerten(se, stimmen, text, max_auswahl=1)
    ba = abstimmung.auswerten(sb, stimmen, text, max_auswahl=2)
    aa = abstimmung.auswerten(sa, stimmen, text, max_auswahl=1)
    art = ea["auswahl"][0] if ea["auswahl"] else None
    codes = ba["auswahl"]
    alter = aa["auswahl"][0] if aa["auswahl"] else "beide"
    labels = {b["id"]: b["label"] for b in bildungsbereiche()["bildungsbereiche"]}
    mapping = {**meta, "status": ba["status"],
               "konfidenz": ba["details"][0]["konfidenz"] if ba["details"] else 0.0,
               "llm_kandidat_bestaetigt": bool(set(codes) & set(llm_bereiche)),
               "bereiche": [{"id": d["code"], "label": labels[d["code"]], **{k: v for k, v in d.items() if k != "code"}}
                            for d in ba["details"]],
               "verworfen": {labels.get(c, c): n for c, n in ba["verworfen"].items()},
               "eignung": {"status": ea["status"], "art": art,
                           "stimmen": {**{z: 0 for z in EIGNUNG_ZIELE}, **{d["code"]: d["stimmen"] for d in ea["details"]},
                                       **ea["verworfen"]},
                           "begruendungen": ea["details"][0]["begruendungen"] if ea["details"] else []},
               "alter": {"status": aa["status"],
                         "stimmen": {**{z: 0 for z in ALTER}, **{d["code"]: d["stimmen"] for d in aa["details"]},
                                     **aa["verworfen"]}}}
    if not codes and llm_bereiche:
        codes = [max(llm_bereiche, key=llm_bereiche.get)]
        mapping.update({"status": "unsicher", "konfidenz": 0.0, "ersatz": "kein Bereich gewählt; Bereich der LLM-Kandidatenliste"})
    k = korr.get(tid)
    if k:
        if k["ausschliessen"]:
            return None, [], alter, mapping, []
        if k["codes"]:
            # korrektur_codes: Bildungsbereich-IDs, optional zusätzlich die Themenart (kinderthema/fachthema)
            codes = [c for c in k["codes"] if c in labels][:2] or codes
            mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
            art = next((c for c in k["codes"] if c in ARTEN), art)
            if art == "ungeeignet" or art is None:
                art = "kinderthema"
            mapping["eignung"]["status"] = "redaktionell"
        if k["lehrebene"] in ALTER:
            alter = k["lehrebene"]
            mapping["alter"]["status"] = "redaktionell"
        mapping["korrektur"] = k
        return art, codes, alter, mapping, []
    gruende = []
    if ea["status"] == "unsicher":
        gruende.append("keine Mehrheit für die Eignung")
    if mapping.get("ersatz"):
        gruende.append("keine Stimme für einen Bildungsbereich")
    elif mapping["status"] == "unsicher":
        gruende.append("keine Mehrheit für einen Bildungsbereich")
    if aa["status"] == "unsicher":
        gruende.append("keine Mehrheit für die Altersgruppe")
    return art, codes, alter, mapping, gruende


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    se_all = abstimmung.lade_stimmen(STIMMEN_EIGNUNG)
    sb_all = abstimmung.lade_stimmen(STIMMEN_BEREICHE)
    sa_all = abstimmung.lade_stimmen(STIMMEN_ALTER)
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH, ebene_spalte="korrektur_alter")
    wlo = lade_wlo()
    labels = {b["id"]: b for b in bildungsbereiche()["bildungsbereiche"]}
    meta = {"methode": METHODE, "modell": modell or (cfg.model if cfg else None), "stimmen": stimmen,
            "datum": date.today().isoformat(),
            "prompt_hash": _hash(KANDIDATEN_SYSTEM + EIGNUNG_REGELN + BEREICHE_REGELN + ALTER_REGELN + "".join(PERSPEKTIVEN))}
    rows, pruef = [], []
    ungeeignet = 0
    for o in _objekte():
        tid, p = o["id"], o["pool"]
        if tid not in se_all:
            continue
        art, codes, alter, mapping, gruende = bewerte(tid, o["text"], se_all[tid], sb_all.get(tid, {}),
                                                      sa_all.get(tid, {}), korr, stimmen, p["llm_bereiche"], meta)
        if art in (None, "ungeeignet") and mapping["eignung"]["status"] != "unsicher" and "korrektur" not in mapping:
            ungeeignet += 1
            continue
        if art in (None, "ungeeignet"):
            art = "kinderthema"  # unsicher: bleibt mit Status unsicher zur Prüfung
        if not codes:
            continue
        w = [wlo[_norm(x)] for x in [p["label"], *p.get("kandidaten", []), *p["synonyme"]] if _norm(x) in wlo]
        # GND-Qualifikatoren wie „Mais <Motiv>“ gehören nicht ins Thema; das GND-Label bleibt unter label_gnd
        row = {"id": tid, "label_de": re.sub(r"\s*<[^>]*>", "", p["label"]).strip(), "label_gnd": p["label"],
               "synonyme": p["synonyme"], "definition": p.get("definition", ""),
               "oberbegriffe": p["oberbegriffe"], "gnd_sachgruppen": p["sachgruppen"], "art": art, "altersgruppe": alter,
               "bildungsbereich": [{"id": c, "uri": bildungsbereiche()["uri_basis"] + c, "label": labels[c]["label"]}
                                   for c in codes],
               "herkunft": sorted({"llm" if p["llm_bereiche"] else None, "wlo" if p["wlo"] else None} - {None}),
               "quelle": {"name": "Gemeinsame Normdatei (GND)", "uri": tid, "lizenz": "CC0-1.0"},
               "mapping": mapping}
        if w:
            nodes = sorted({n for e in w for n in e["nodes"]})
            row["belege"] = {"wlo": {"quelle": WLO_QUELLE, "datensaetze": max(e["anzahl"] for e in w),
                                     "nodes": nodes[:10]}}
        rows.append(row)
        if gruende:
            pruef.append({"id": tid, "label_de": p["label"], "grund": "; ".join(gruende),
                          "art": f"{art} ({mapping['eignung']['stimmen']})",
                          "bereiche": " | ".join(f"{b['id']} ({b['stimmen']}/{stimmen})" for b in mapping["bereiche"])
                          or " | ".join(codes) + " (Ersatz)",
                          "alter": f"{alter} ({mapping['alter']['stimmen']})"})
    rows.sort(key=lambda r: (r["bildungsbereich"][0]["id"], r["label_de"].casefold()))
    _write_jsonl(THEMEN_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "korrektur_alter", "kommentar"])
    return fertig(f"{len(rows)} Themen, {ungeeignet} als ungeeignet verworfen, {len(pruef)} auf der Prüfliste")


def statistik(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else (load_jsonl(THEMEN_PATH) if THEMEN_PATH.exists() else [])
    m = [r.get("mapping", {}) for r in rows]
    return {
        "themen": len(rows),
        "art": dict(Counter(r["art"] for r in rows)),
        "altersgruppe": dict(Counter(r["altersgruppe"] for r in rows)),
        "bildungsbereich": dict(Counter(r["bildungsbereich"][0]["id"] for r in rows)),
        "status_bereich": dict(Counter(x.get("status") for x in m)),
        "status_eignung": dict(Counter((x.get("eignung") or {}).get("status") for x in m)),
        "status_alter": dict(Counter((x.get("alter") or {}).get("status") for x in m)),
        "herkunft": dict(Counter("+".join(r["herkunft"]) for r in rows)),
        "mit_wlo_beleg": sum(1 for r in rows if r.get("belege")),
    }
