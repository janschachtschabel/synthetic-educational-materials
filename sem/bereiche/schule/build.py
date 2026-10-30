"""Baut die Schul-Mappings unter data/mappings/schule/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  themen           WLO-Themenbäume (OEH-Vokabular oehTopics) laden: Fachportal, Pfad, Fach, Definition, Keywords
  wlo              je Thema die Bildungsstufen der WLO-Datensätze, die es in ccm:curriculum tragen (Export bleibt lokal)
  gnd              Themenlabels exakt auf GND-Sachschlagwörter abgleichen (lobid), als zusätzliche Normierung
  art              drei Stimmen: Unterrichtsthema, Thema für Lehrkräfte oder ungeeignet (Sammel- und Ordnungsknoten)
  stufen           drei Stimmen: in welchen Schulstufen das Thema unterrichtet wird (Primarstufe, Sek I, Sek II)
  zusammenfuehren  themen_schule.jsonl, pruefliste_themen.csv; korrekturen.csv hat Vorrang
"""

from __future__ import annotations

import csv
import hashlib
import json
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ...llm import LLMConfig
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...paths import DATA_DIR
from ...quellen import gnd
from ...vocabs import OEH_VOCABS_RAW
from ..base import fertig
from . import DIR, STUFEN, load_jsonl

CACHE = DATA_DIR / "cache"
OEH_TOPICS = CACHE / "oeh" / "oehTopics.ttl"
TOPICS_SCHEME = "http://w3id.org/openeduhub/vocabs/oeh-topics/5e40e372-735c-4b17-bbf7-e827a5702b57"
ROH_PATH = CACHE / "schule_themen_roh.jsonl"
WLO_PATH = CACHE / "wlo_stufen_schule.json"
GND_PATH = CACHE / "schule_gnd.json"
STIMMEN_ART = CACHE / "schule_stimmen_art.jsonl"
STIMMEN_STUFEN = CACHE / "schule_stimmen_stufen.jsonl"
THEMEN_PATH = DIR / "themen_schule.jsonl"
PRUEFLISTE_PATH = DIR / "pruefliste_themen.csv"
KORREKTUREN_PATH = DIR / "korrekturen.csv"
QUELLE = {"name": "WirLernenOnline-Themenbäume (OEH-Vokabular oehTopics, Taxonomie von Lehrplanthemen)",
          "uri": "http://w3id.org/openeduhub/vocabs/oeh-topics/", "lizenz": "CC0-1.0"}
WLO_STUFEN = {"Primarstufe": "primar", "Sekundarstufe I": "sek1", "Sekundarstufe II": "sek2"}
N_STIMMEN = 3


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


# --- 1. Themen aus den WLO-Themenbäumen ------------------------------------------------------------

def build_themen(cfg: LLMConfig | None = None, **_):
    import rdflib
    from rdflib.namespace import SKOS

    if not OEH_TOPICS.exists():
        OEH_TOPICS.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(OEH_VOCABS_RAW.format(name="oehTopics"), OEH_TOPICS)
    g = rdflib.Graph()
    g.parse(OEH_TOPICS)
    sdo_kw = rdflib.URIRef("https://schema.org/keywords")

    def label(s):
        return str(g.value(s, SKOS.prefLabel) or "")

    def pfad(s):
        out = []
        while (b := g.value(s, SKOS.broader)) is not None:
            out.insert(0, b)
            s = b
        return out

    rows = []
    for s in g.subjects(rdflib.RDF.type, SKOS.Concept):
        p = pfad(s)
        if not p:
            continue  # Fachportal selbst (Top-Konzept)
        portal = p[0]
        faecher = [str(o) for o in g.objects(s, SKOS.relatedMatch) if "/discipline/" in str(o)] \
            or [str(o) for o in g.objects(portal, SKOS.relatedMatch) if "/discipline/" in str(o)]
        rows.append({"id": str(s), "label_de": label(s), "portal": label(portal), "portal_uri": str(portal),
                     "pfad": [label(x) for x in p[1:]], "ebene": len(p),
                     "unterthemen": len(list(g.objects(s, SKOS.narrower))),
                     "definition": str(g.value(s, SKOS.definition) or "").strip(),
                     "keywords": sorted({str(k).strip() for k in g.objects(s, sdo_kw) if str(k).strip()})[:15],
                     "faecher": list(dict.fromkeys(faecher))})
    rows.sort(key=lambda r: (r["portal"], r["pfad"], r["label_de"]))
    _write_jsonl(ROH_PATH, rows)
    return fertig(f"{len(rows)} Themen aus {len({r['portal'] for r in rows})} Fachportalen")


def lade_roh(limit: int | None = None) -> list[dict]:
    if not ROH_PATH.exists():
        raise FileNotFoundError(f"{ROH_PATH.name} fehlt: erst den Schritt „themen“ ausführen.")
    return load_jsonl(ROH_PATH)[:limit]


# --- 2. WLO: Bildungsstufen der Datensätze je Thema ------------------------------------------------

WLO_QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
              "hinweis": "nur Themenbaum-Zuordnung (ccm:curriculum) und Bildungsstufe der Datensätze genutzt; "
                         "Beleg als Zahl der Datensätze je Stufe und Node-IDs"}


def build_wlo(cfg: LLMConfig | None = None, wlo_dateien: list[str] | None = None, **_):
    from ...quellen.wlo import _liste

    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    csv.field_size_limit(10 ** 9)
    data: dict = defaultdict(lambda: {"stufen": Counter(), "nodes": set()})
    for path in wlo_dateien:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=";")

            def spalte(name):
                return next((c for c in r.fieldnames if c == name or c.endswith("." + name)), None)

            c_node, c_ctx, c_cur = spalte("sys:node-uuid"), spalte("ccm:educationalcontext_DISPLAYNAME"), \
                spalte("ccm:curriculum")
            for i, row in enumerate(r):
                themen = [u for u in _liste(row, c_cur) if "/oeh-topics/" in u]
                if not themen:
                    continue
                stufen = {WLO_STUFEN[c] for c in _liste(row, c_ctx) if c in WLO_STUFEN}
                node = row[c_node].strip() if c_node else f"#{Path(path).name}:{i}"
                for t in themen:
                    data[t]["nodes"].add(node)
                    data[t]["stufen"].update(stufen)
    rows = {t: {"datensaetze": len(v["nodes"]), "stufen": dict(v["stufen"]),
                "nodes": sorted(n for n in v["nodes"] if not n.startswith("#"))[:10]} for t, v in data.items()}
    WLO_PATH.parent.mkdir(parents=True, exist_ok=True)
    WLO_PATH.write_text(json.dumps({"quelle": WLO_QUELLE, "themen": rows}, ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(rows)} Themen mit WLO-Datensätzen")


def lade_wlo() -> dict:
    return json.loads(WLO_PATH.read_text(encoding="utf-8"))["themen"] if WLO_PATH.exists() else {}


def wlo_stufen(w: dict | None, min_anteil: float = 0.2, min_datensaetze: int = 3) -> list[str]:
    """Stufen, die in mindestens min_anteil der WLO-Datensätze eines Themas vorkommen."""
    if not w or w["datensaetze"] < min_datensaetze:
        return []
    return [s for s in STUFEN if w["stufen"].get(s, 0) >= min_anteil * w["datensaetze"]]


# --- 3. GND-Abgleich -------------------------------------------------------------------------------

def build_gnd(cfg: LLMConfig | None = None, workers: int = 6, **_):
    themen = lade_roh()

    def suche(t):
        rec = gnd.suche_exakt(t["label_de"])
        return t["id"], ({"uri": rec["uri"], "label": rec["label"]} if rec and gnd.ist_thema(rec) else None)

    with ThreadPoolExecutor(workers) as ex:
        out = {tid: rec for tid, rec in ex.map(suche, themen) if rec}
    GND_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(out)} von {len(themen)} Themen exakt einem GND-Sachbegriff zugeordnet")


def lade_gnd() -> dict:
    return json.loads(GND_PATH.read_text(encoding="utf-8")) if GND_PATH.exists() else {}


# --- Objekte für die Abstimmung --------------------------------------------------------------------

def _objekte(limit: int | None = None) -> list[dict]:
    out = []
    for t in lade_roh(limit):
        prompt = {"thema": t["label_de"], "fachportal": t["portal"], "pfad": " > ".join(t["pfad"]) or "-",
                  "unterthemen": t["unterthemen"], "keywords": t["keywords"][:8]}
        if t["definition"]:
            prompt["beschreibung"] = t["definition"][:400]
        text = " ".join([t["label_de"], t["portal"], " ".join(t["pfad"]), t["definition"], " ".join(t["keywords"])])
        out.append({"id": t["id"], "thema": t, "prompt": prompt, "text": text})
    return out


def _systeme(perspektiven: list[str], regeln: str, n: int) -> list[str]:
    return [perspektiven[i % len(perspektiven)] + "\n\n" + regeln for i in range(n)]


PERSPEKTIVEN = [
    "Du bist erfahrene Lehrkraft und kennst die Lehrpläne der Länder.",
    "Du bist Fachredakteurin eines Bildungsportals für Schulen.",
    "Du bist Fachleiter in der Lehrkräfteausbildung.",
]

# --- 4. Themenart ----------------------------------------------------------------------------------

ART_ZIELE = {
    "unterrichtsthema": Ziel("unterrichtsthema", "unterrichtsthema", "Unterrichtsthema",
                             "Inhalt, den Schülerinnen und Schüler im Fachunterricht lernen"),
    "lehrkraftthema": Ziel("lehrkraftthema", "lehrkraftthema", "Thema für Lehrkräfte",
                           "Didaktik, Methodik, Unterrichtsorganisation, Schulentwicklung, Lehrerbildung"),
    "ungeeignet": Ziel("ungeeignet", "ungeeignet", "ungeeignet",
                       "Sammel- oder Ordnungsknoten ohne eigenen Inhalt (z. B. Allgemeines, Sonstiges, Linksammlung)"),
}

ART_REGELN = """Die Themen stammen aus den Themenbäumen der Fachportale von WirLernenOnline. Ordne jedes Thema genau einer Kategorie zu (code aus den Kandidaten).
- "unterrichtsthema": ein fachlicher Inhalt, zu dem es Unterrichtsmaterial für Schülerinnen und Schüler gibt.
- "lehrkraftthema": ein Thema, das vor allem Lehrkräfte betrifft (Didaktik, Methoden, Organisation, Recht, Schulentwicklung).
- "ungeeignet": ein reiner Sammel- oder Ordnungsknoten ohne bestimmbaren Inhalt.
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus Thema, Pfad, Beschreibung oder Keywords an."""


def build_art(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
              stimmen: int = N_STIMMEN, **_):
    objekte = _objekte(limit)
    alle = list(ART_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, ART_ZIELE,
                                _systeme(PERSPEKTIVEN, ART_REGELN, stimmen), STIMMEN_ART, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 5. Schulstufen --------------------------------------------------------------------------------

STUFEN_ZIELE = {
    "primar": Ziel("primar", "primar", "Primarstufe", "Grundschule, Klasse 1–4"),
    "sek1": Ziel("sek1", "sek1", "Sekundarstufe I", "Klasse 5–10"),
    "sek2": Ziel("sek2", "sek2", "Sekundarstufe II", "gymnasiale Oberstufe, Klasse 11–13"),
}

STUFEN_REGELN = """Wähle für jedes Thema die Schulstufen, in denen es in Deutschland typischerweise unterrichtet wird (codes aus den Kandidaten, die wichtigste zuerst, höchstens drei).
- Nur Stufen, in denen das Thema laut Lehrplänen tatsächlich vorkommt; ein Grundlagenthema kann in mehreren Stufen vorkommen.
- Bei Themen für Lehrkräfte: die Stufen, auf die sich das Thema bezieht.
Je Stufe: "begruendung" (ein kurzer Satz) und 1 bis 3 "belegbegriffe" aus Thema, Pfad, Beschreibung oder Keywords."""


def build_stufen(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                 stimmen: int = N_STIMMEN, **_):
    st = abstimmung.lade_stimmen(STIMMEN_ART)
    objekte = [o for o in _objekte(limit)
               if (a := abstimmung.auswerten(st.get(o["id"], {}), N_STIMMEN, max_auswahl=1))["auswahl"]
               and a["auswahl"][0] != "ungeeignet"]
    alle = list(STUFEN_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, STUFEN_ZIELE,
                                _systeme(PERSPEKTIVEN, STUFEN_REGELN, stimmen), STIMMEN_STUFEN, cfg,
                                max_auswahl=3, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 6. Zusammenführen -----------------------------------------------------------------------------

METHODE = ("Themen aus den WLO-Themenbäumen (oehTopics); LLM-Mehrfachabstimmung zu Themenart und Schulstufen, "
           "Gegenprobe über die Bildungsstufen der WLO-Datensätze je Thema")


def bewerte(tid: str, text: str, sa: dict, ss: dict, korr: dict, stimmen: int, w: dict | None, meta: dict):
    aa = abstimmung.auswerten(sa, stimmen, text, max_auswahl=1)
    sta = abstimmung.auswerten(ss, stimmen, text, max_auswahl=3)
    art = aa["auswahl"][0] if aa["auswahl"] else None
    stufen = [s for s in STUFEN if s in sta["auswahl"]]
    belegt = wlo_stufen(w)
    mapping = {**meta, "status": sta["status"],
               "konfidenz": sta["details"][0]["konfidenz"] if sta["details"] else 0.0,
               "wlo_bestaetigt": bool(set(stufen) & set(belegt)),
               "stufen": [{"id": d["code"], **{k: v for k, v in d.items() if k != "code"}} for d in sta["details"]],
               "verworfen": dict(sta["verworfen"]),
               "art": {"status": aa["status"],
                       "stimmen": {**{z: 0 for z in ART_ZIELE}, **{d["code"]: d["stimmen"] for d in aa["details"]},
                                   **aa["verworfen"]},
                       "begruendungen": aa["details"][0]["begruendungen"] if aa["details"] else []}}
    if not stufen and belegt and art not in (None, "ungeeignet"):
        stufen = belegt
        mapping.update({"status": "unsicher", "konfidenz": 0.0, "wlo_bestaetigt": True,
                        "ersatz": "keine Stufe gewählt; Stufen laut WLO-Datensätzen"})
    k = korr.get(tid)
    if k:
        if k["ausschliessen"]:
            return None, [], mapping, []
        if k["codes"]:
            stufen = [s for s in STUFEN if s in k["codes"]] or stufen
            mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
        if k["lehrebene"] in ("unterrichtsthema", "lehrkraftthema"):
            art = k["lehrebene"]
            mapping["art"]["status"] = "redaktionell"
        if art in (None, "ungeeignet"):
            art = "unterrichtsthema"
        mapping["korrektur"] = k
        return art, stufen, mapping, []
    gruende = []
    if aa["status"] == "unsicher":
        gruende.append("keine Mehrheit für die Themenart")
    if mapping.get("ersatz"):
        gruende.append("keine Stimme für eine Schulstufe, Stufen nur aus WLO")
    elif sta["status"] == "unsicher" and art != "ungeeignet":
        gruende.append("keine Mehrheit für eine Schulstufe")
    if belegt and stufen and not mapping["wlo_bestaetigt"]:
        gruende.append("Stufen weichen von den WLO-Datensätzen ab")
    return art, stufen, mapping, gruende


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    sa_all, ss_all = abstimmung.lade_stimmen(STIMMEN_ART), abstimmung.lade_stimmen(STIMMEN_STUFEN)
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH, ebene_spalte="korrektur_art")
    wlo, gnd_map = lade_wlo(), lade_gnd()
    meta = {"methode": METHODE, "modell": modell or (cfg.model if cfg else None), "stimmen": stimmen,
            "datum": date.today().isoformat(),
            "prompt_hash": _hash(ART_REGELN + STUFEN_REGELN + "".join(PERSPEKTIVEN))}
    rows, pruef = [], []
    ungeeignet = 0
    for o in _objekte():
        tid, t = o["id"], o["thema"]
        if tid not in sa_all:
            continue
        art, stufen, mapping, gruende = bewerte(tid, o["text"], sa_all[tid], ss_all.get(tid, {}), korr, stimmen,
                                                wlo.get(tid), meta)
        if art in (None, "ungeeignet") and mapping["art"]["status"] != "unsicher" and "korrektur" not in mapping:
            ungeeignet += 1
            continue
        if not stufen:
            continue
        row = {**{k: t[k] for k in ("id", "label_de", "portal", "portal_uri", "pfad", "ebene", "definition", "keywords",
                                    "faecher")},
               "art": art if art in ("unterrichtsthema", "lehrkraftthema") else "unterrichtsthema",
               "stufen": stufen, "quelle": QUELLE, "mapping": mapping}
        if tid in gnd_map:
            row["gnd"] = gnd_map[tid]
        if wlo.get(tid):
            row["belege"] = {"wlo": {"quelle": WLO_QUELLE, **wlo[tid]}}
        rows.append(row)
        if gruende:
            pruef.append({"id": tid, "label_de": t["label_de"], "portal": t["portal"], "pfad": " > ".join(t["pfad"]),
                          "grund": "; ".join(gruende), "art": f"{row['art']} ({mapping['art']['stimmen']})",
                          "stufen": " | ".join(f"{s['id']} ({s['stimmen']}/{stimmen})" for s in mapping["stufen"])
                          or " | ".join(stufen) + " (WLO)",
                          "wlo_stufen": json.dumps((wlo.get(tid) or {}).get("stufen", {}), ensure_ascii=False)})
    _write_jsonl(THEMEN_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "korrektur_art", "kommentar"])
    return fertig(f"{len(rows)} Themen, {ungeeignet} als Sammelknoten verworfen, {len(pruef)} auf der Prüfliste")


def statistik(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else (load_jsonl(THEMEN_PATH) if THEMEN_PATH.exists() else [])
    m = [r.get("mapping", {}) for r in rows]
    return {
        "themen": len(rows),
        "art": dict(Counter(r["art"] for r in rows)),
        "fachportale": len({r["portal"] for r in rows}),
        "stufen": dict(Counter(s for r in rows for s in r["stufen"])),
        "status_stufen": dict(Counter(x.get("status") for x in m)),
        "status_art": dict(Counter((x.get("art") or {}).get("status") for x in m)),
        "wlo_bestaetigt": sum(1 for x in m if x.get("wlo_bestaetigt")),
        "mit_wlo_beleg": sum(1 for r in rows if r.get("belege")),
        "mit_gnd": sum(1 for r in rows if r.get("gnd")),
    }
