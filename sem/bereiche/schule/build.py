"""Baut die Schul-Mappings unter data/mappings/schule/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  themen           WLO-Themenbäume (OEH-Vokabular oehTopics) laden: Fachportal, Pfad, Fach, Definition, Keywords
  wlo              je Thema die Bildungsstufen der WLO-Datensätze, die es in ccm:curriculum tragen (Export bleibt lokal)
  gnd              Themenlabels exakt auf GND-Sachschlagwörter abgleichen (lobid), als zusätzliche Normierung
  art              drei Stimmen: Unterrichtsthema, Thema für Lehrkräfte oder ungeeignet (Sammel- und Ordnungsknoten)
  stufen           drei Stimmen: in welchen Schulstufen das Thema unterrichtet wird (Primarstufe, Sek I, Sek II)
  keywords         Keywords der WLO-Schuldatensätze je Fach zählen (Export bleibt lokal; nur Keywords, Fach,
                   Bildungsstufe, Themenbaum-Zuordnung und Node-ID)
  kw_gnd           Keywords exakt auf GND-Sachbegriffe; Treffer gleich einem Themenbaum-Thema werden dessen Beleg,
                   die übrigen neue, feinere Themen
  kw_bedeutung     drei Stimmen, nur wenn das Keyword bloß einem GND-Synonym entspricht (z. B. Abkürzungen):
                   bedeutet es in diesen Fächern dasselbe wie der GND-Begriff? Sonst fällt das Thema weg
  kw_art           drei Stimmen: Unterrichtsthema, Thema für Lehrkräfte oder ungeeignet (zu allgemein, kein Fachinhalt)
  kw_fach          drei Stimmen: Schulfach aus den Fächern der WLO-Datensätze (bis zu zwei)
  kw_stufen        drei Stimmen: Schulstufen wie oben, Gegenprobe über die Bildungsstufen der WLO-Datensätze
  zusammenfuehren  themen_schule.jsonl, pruefliste_themen.csv; korrekturen.csv hat Vorrang
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ...llm import LLMConfig, Progress
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
KW_PATH = CACHE / "wlo_keywords_schule.json"
KW_POOL_PATH = CACHE / "schule_kw_pool.jsonl"
STIMMEN_KW_ART = CACHE / "schule_stimmen_kw_art.jsonl"
STIMMEN_KW_FACH = CACHE / "schule_stimmen_kw_fach.jsonl"
STIMMEN_KW_STUFEN = CACHE / "schule_stimmen_kw_stufen.jsonl"
STIMMEN_KW_BEDEUTUNG = CACHE / "schule_stimmen_kw_bedeutung.jsonl"
GND_QUELLE = {"name": "Gemeinsame Normdatei (GND), Sachbegriffe über lobid-gnd", "uri": "https://lobid.org/gnd",
              "lizenz": "CC0-1.0"}
WLO_STUFEN = {"Primarstufe": "primar", "Sekundarstufe I": "sek1", "Sekundarstufe II": "sek2"}
N_STIMMEN = 3
KW_MIN_NODES = 3        # Keyword muss in so vielen Schuldatensätzen vorkommen
KW_FACH_ANTEIL = 0.1    # Fach als Kandidat, wenn es in mindestens 10 % der Datensätze des Keywords steht
KW_ELTERN_ANTEIL = 0.2
# Stufen von Fächern ohne eigenes Fachportal im Themenbaum
FACH_STUFEN = {"Sachunterricht": {"primar"}}  # Themenbaum-Thema als Oberthema, wenn es in mindestens 20 % der Datensätze steht


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
    from ...quellen.wlo import datensaetze

    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    data: dict = defaultdict(lambda: {"stufen": Counter(), "nodes": set()})
    for node, w in datensaetze(wlo_dateien, ["kontext", "curriculum"]):
        themen = [u for u in w["curriculum"] if "/oeh-topics/" in u]
        if not themen:
            continue
        stufen = {WLO_STUFEN[c] for c in w["kontext"] if c in WLO_STUFEN}
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


# --- 6. Themen aus WLO-Keywords je Fach ------------------------------------------------------------

KW_QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
             "hinweis": "nur Keywords, Fach, Bildungsstufe und Themenbaum-Zuordnung der Schuldatensätze genutzt; "
                        "übernommen wird nur, was exakt einem GND-Sachbegriff entspricht; Beleg als Zahl der "
                        "Datensätze und Node-IDs"}


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def build_keywords(cfg: LLMConfig | None = None, wlo_dateien: list[str] | None = None, **_):
    from ...quellen.wlo import datensaetze

    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    data: dict = defaultdict(lambda: {"schreibweisen": Counter(), "nodes": set(), "faecher": Counter(),
                                      "stufen": Counter(), "themen": Counter()})
    for node, w in datensaetze(wlo_dateien, ["kontext", "curriculum", "fach", "keywords"]):
        stufen = {WLO_STUFEN[c] for c in w["kontext"] if c in WLO_STUFEN}
        faecher = [u for u in w["fach"] if "/discipline/" in u]
        if not stufen or not faecher:
            continue
        themen = [u for u in w["curriculum"] if "/oeh-topics/" in u]
        for kw in set(w["keywords"]):
            e = data[_norm(kw)]
            e["schreibweisen"][kw] += 1
            e["nodes"].add(node)
            e["faecher"].update(faecher)
            e["stufen"].update(stufen)
            e["themen"].update(themen)
    rows = {}
    for k, v in data.items():
        if len(v["nodes"]) >= KW_MIN_NODES:
            rows[k] = {"schreibweise": v["schreibweisen"].most_common(1)[0][0], "datensaetze": len(v["nodes"]),
                       "faecher": dict(v["faecher"]), "stufen": dict(v["stufen"]),
                       "themen": dict(v["themen"].most_common(10)),
                       "nodes": sorted(n for n in v["nodes"] if not n.startswith("#"))[:10]}
    KW_PATH.parent.mkdir(parents=True, exist_ok=True)
    KW_PATH.write_text(json.dumps({"quelle": KW_QUELLE, "min_nodes": KW_MIN_NODES, "keywords": rows},
                                  ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(rows)} Keywords in mindestens {KW_MIN_NODES} Schuldatensätzen")


def lade_keywords() -> dict:
    return json.loads(KW_PATH.read_text(encoding="utf-8"))["keywords"] if KW_PATH.exists() else {}


def _fach_portale() -> dict[str, dict]:
    """discipline-URI → häufigstes Fachportal der Themenbaum-Themen mit diesem Fach."""
    zaehl: dict = defaultdict(Counter)
    for t in lade_roh():
        for u in t["faecher"]:
            zaehl[u][(t["portal"], t["portal_uri"])] += 1
    return {u: dict(zip(("portal", "portal_uri"), c.most_common(1)[0][0])) for u, c in zaehl.items()}


def build_kw_gnd(cfg: LLMConfig | None = None, on_progress=None, workers: int = 6, **_):
    from ...vocabs import load as load_vocab

    kws = lade_keywords()
    if not kws:
        raise FileNotFoundError(f"{KW_PATH.name} fehlt: erst den Schritt „keywords“ mit --wlo ausführen.")
    roh = lade_roh()
    baum_label = {_norm(t["label_de"]): t["id"] for t in roh}
    baum_gnd = {g["uri"]: tid for tid, g in lade_gnd().items()}
    disciplines = load_vocab("discipline")
    zu_allgemein = {_norm(c["label"]) for c in disciplines.concepts} | {_norm(t["portal"]) for t in roh}
    prog = Progress(total=len(kws), status="GND-Abgleich der Keywords")

    def suche(item):
        try:
            return item, gnd.suche_exakt(item[1]["schreibweise"])
        except Exception:  # lobid zeitweise nicht erreichbar: beim nächsten Lauf erneut (Treffer sind zwischengespeichert)
            prog.failed += 1
            return item, None

    pool: dict[str, dict] = {}
    baum_belege: dict = defaultdict(lambda: {"keywords": [], "datensaetze": 0})
    with ThreadPoolExecutor(workers) as ex:
        for (norm, k), rec in ex.map(suche, kws.items()):
            prog.done += 1
            if on_progress and prog.done % 200 == 0:
                on_progress(prog)
            if norm in zu_allgemein:
                continue
            tid = baum_label.get(norm) or (rec and baum_gnd.get(rec["uri"]))
            if tid:
                baum_belege[tid]["keywords"].append(k["schreibweise"])
                baum_belege[tid]["datensaetze"] = max(baum_belege[tid]["datensaetze"], k["datensaetze"])
                continue
            if not rec or not gnd.ist_thema(rec) or _norm(rec["label"]) in zu_allgemein:
                continue
            e = pool.setdefault(rec["uri"], {**rec, "keywords": [], "datensaetze": 0, "faecher": Counter(),
                                             "stufen": Counter(), "themen": Counter(), "nodes": set()})
            e["keywords"].append(k["schreibweise"])
            e["datensaetze"] += k["datensaetze"]  # obere Schranke, wenn mehrere Schreibweisen zusammenfallen
            for f in ("faecher", "stufen", "themen"):
                e[f].update(k[f])
            e["nodes"].update(k["nodes"])
    rows = [{**v, "faecher": dict(v["faecher"]), "stufen": dict(v["stufen"]), "themen": dict(v["themen"]),
             "nodes": sorted(v["nodes"])[:10]} for v in pool.values()]
    rows.sort(key=lambda r: r["label"].casefold())
    _write_jsonl(KW_POOL_PATH, rows)
    (CACHE / "schule_kw_baum_belege.json").write_text(json.dumps(baum_belege, ensure_ascii=False), encoding="utf-8")
    if prog.failed > 0.01 * len(kws):
        raise RuntimeError(f"{prog.failed} GND-Abfragen fehlgeschlagen (lobid); Schritt „kw_gnd“ erneut starten")
    return fertig(f"{len(kws)} Keywords: {len(rows)} neue GND-Themen, {len(baum_belege)} Themenbaum-Themen belegt"
                  + (f", {prog.failed} Abfragen fehlgeschlagen" if prog.failed else ""))


def lade_kw_pool(limit: int | None = None) -> list[dict]:
    if not KW_POOL_PATH.exists():
        raise FileNotFoundError(f"{KW_POOL_PATH.name} fehlt: erst den Schritt „kw_gnd“ ausführen.")
    return load_jsonl(KW_POOL_PATH)[:limit]


def lade_kw_baum_belege() -> dict:
    p = CACHE / "schule_kw_baum_belege.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _fach_kandidaten(p: dict) -> list[str]:
    gesamt = p["datensaetze"] or 1
    return [u for u, n in sorted(p["faecher"].items(), key=lambda x: -x[1]) if n >= KW_FACH_ANTEIL * gesamt][:4] \
        or [max(p["faecher"], key=p["faecher"].get)]


def _fach_ziele() -> dict[str, Ziel]:
    from ...vocabs import load as load_vocab

    d = load_vocab("discipline")
    return {u.rsplit("/", 1)[-1]: Ziel(u.rsplit("/", 1)[-1], u, c["label"]) for u, c in d.by_uri.items()}


def _kw_objekte(limit: int | None = None) -> list[dict]:
    ziele = {z.uri: z for z in _fach_ziele().values()}
    out = []
    for p in lade_kw_pool(limit):
        label = re.sub(r"\s*<[^>]*>", "", p["label"]).strip()
        prompt = {"thema": label, "gnd_begriff": p["label"], "keywords_in_wlo": p["keywords"][:3], "faecher_in_wlo": [ziele[u].label for u in _fach_kandidaten(p) if u in ziele]}
        if p["oberbegriffe"]:
            prompt["oberbegriffe"] = [b["label"] for b in p["oberbegriffe"]][:3]
        if p["synonyme"]:
            prompt["synonyme"] = p["synonyme"][:5]
        if p["definition"]:
            prompt["beschreibung"] = p["definition"][:400]
        text = " ".join([label, p["definition"], " ".join(p["synonyme"]), " ".join(prompt["faecher_in_wlo"]),
                         " ".join(prompt.get("oberbegriffe", []))])
        out.append({"id": p["uri"], "thema": {**p, "label_de": label}, "prompt": prompt, "text": text})
    return out


KW_ART_REGELN = """Die Begriffe sind Schlagwörter aus Unterrichtsmaterialien von WirLernenOnline, normiert als GND-Sachbegriffe; "faecher_in_wlo" nennt die Schulfächer der Materialien. Ordne jeden Begriff genau einer Kategorie zu (code aus den Kandidaten).
- "unterrichtsthema": ein fachlicher Inhalt, der im Unterricht eines dieser Fächer an allgemeinbildenden Schulen behandelt wird und sich als Thema eines Unterrichtsmaterials eignet.
- "lehrkraftthema": ein Thema, das vor allem Lehrkräfte betrifft (Didaktik, Methoden, Organisation, Recht, Schulentwicklung).
- "ungeeignet": zu allgemein (z. B. ein ganzes Fach, "Unterricht", "Lernen", "Bildung"), eine Materialart oder ein Format (z. B. Arbeitsblatt, Video, Quiz, Erklärung, Zusammenfassung), ein Gefühl oder eine Bewertung (z. B. Spaß), ein Werkzeug oder Produkt ohne fachlichen Inhalt, oder kein Thema der Schulfächer.
- "gnd_begriff" zeigt den normierten Begriff, ggf. mit Zusatz in <…>; "keywords_in_wlo" die Schreibweisen in den Materialien. Bezeichnet der GND-Begriff offensichtlich etwas anderes als das Schlagwort in diesen Fächern (z. B. "German <Brauch>" für das Schlagwort "German" im Englischunterricht), ist er "ungeeignet".
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus Thema, Synonymen, Oberbegriffen oder Beschreibung an."""

KW_FACH_REGELN = """Die Begriffe sind Themen aus Unterrichtsmaterialien von WirLernenOnline. Wähle für jeden Begriff das Schulfach, in dem er an allgemeinbildenden Schulen in Deutschland vor allem unterrichtet wird (codes aus den Kandidaten, die wichtigste zuerst, höchstens zwei). Ein zweites Fach nur, wenn das Thema dort ebenso fest im Lehrplan steht.
Je Fach: "begruendung" (ein kurzer Satz) und 1 bis 3 "belegbegriffe" aus Thema, Synonymen, Oberbegriffen oder Beschreibung."""


def nur_synonym(p: dict) -> bool:
    """Keyword entspricht nicht dem Vorzugsnamen, sondern nur einem Synonym des GND-Begriffs."""
    label = _norm(re.sub(r"\s*<[^>]*>", "", p["label"]))
    return not any(_norm(k) == label for k in p["keywords"])


BEDEUTUNG_ZIELE = {
    "gleich": Ziel("gleich", "gleich", "gleiche Bedeutung",
                   "das Schlagwort meint in Materialien dieser Fächer denselben Begriff wie der GND-Begriff"),
    "anders": Ziel("anders", "anders", "andere Bedeutung",
                   "das Schlagwort meint dort etwas anderes (z. B. andere Auflösung einer Abkürzung, englisches Wort, "
                   "Homonym)"),
}

BEDEUTUNG_REGELN = """Jedes Schlagwort ("keywords_in_wlo") stammt aus Unterrichtsmaterialien der genannten Schulfächer ("faecher_in_wlo") und wurde über ein Synonym dem GND-Begriff ("gnd_begriff", mit Synonymen und Beschreibung) zugeordnet. Entscheide, ob das Schlagwort in Materialien dieser Fächer typischerweise denselben Begriff meint (code aus den Kandidaten).
- "gleich": z. B. "BMI" in Biologie → Body-Mass-Index, "Urin" → Harn.
- "anders": z. B. "cars" → CARS-Spektroskopie (gemeint sind Autos), "ISS" in Physik → Ionenstreuspektroskopie (gemeint ist die Raumstation), "age" → Advanced glycosylation end products.
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" an."""


def build_kw_bedeutung(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                       stimmen: int = N_STIMMEN, **_):
    objekte = [o for o in _kw_objekte(limit) if nur_synonym(o["thema"])]
    alle = list(BEDEUTUNG_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, BEDEUTUNG_ZIELE,
                                _systeme(PERSPEKTIVEN, BEDEUTUNG_REGELN, stimmen), STIMMEN_KW_BEDEUTUNG, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


def _kw_art_ok(limit: int | None = None) -> list[dict]:
    st = abstimmung.lade_stimmen(STIMMEN_KW_ART)
    return [o for o in _kw_objekte(limit)
            if (a := abstimmung.auswerten(st.get(o["id"], {}), N_STIMMEN, max_auswahl=1))["auswahl"]
            and a["auswahl"][0] != "ungeeignet"]


def build_kw_art(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                 stimmen: int = N_STIMMEN, **_):
    objekte = _kw_objekte(limit)
    alle = list(ART_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, ART_ZIELE,
                                _systeme(PERSPEKTIVEN, KW_ART_REGELN, stimmen), STIMMEN_KW_ART, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


def build_kw_fach(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                  stimmen: int = N_STIMMEN, **_):
    ziele = _fach_ziele()
    code = {z.uri: c for c, z in ziele.items()}
    objekte = _kw_art_ok(limit)
    kand = {o["id"]: [code[u] for u in _fach_kandidaten(o["thema"]) if u in code] for o in objekte}
    objekte = [o for o in objekte if kand[o["id"]]]
    return abstimmung.abstimmen(objekte, kand, ziele, _systeme(PERSPEKTIVEN, KW_FACH_REGELN, stimmen),
                                STIMMEN_KW_FACH, cfg, max_auswahl=2, batch=20, on_progress=on_progress,
                                stop_event=stop_event)


def build_kw_stufen(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                    stimmen: int = N_STIMMEN, **_):
    objekte = _kw_art_ok(limit)
    alle = list(STUFEN_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, STUFEN_ZIELE,
                                _systeme(PERSPEKTIVEN, STUFEN_REGELN.replace("Pfad, Beschreibung oder Keywords",
                                                                             "Synonymen, Oberbegriffen oder Beschreibung"),
                                         stimmen),
                                STIMMEN_KW_STUFEN, cfg, max_auswahl=3, batch=20, on_progress=on_progress,
                                stop_event=stop_event)


KW_METHODE = ("Schlagwort aus WLO-Schuldatensätzen (mindestens drei Datensätze), exakt einem GND-Sachbegriff "
              "zugeordnet; LLM-Mehrfachabstimmung zu Themenart, Schulfach (aus den Fächern der Datensätze) und "
              "Schulstufen, Gegenprobe über die Bildungsstufen der Datensätze")


def _portal_stufen(baum_rows: list[dict], min_anteil: float = 0.05) -> dict[str, set[str]]:
    """Schulstufen, die in einem Fachportal vorkommen (mindestens 5 % seiner Themenbaum-Themen)."""
    zaehl: dict = defaultdict(Counter)
    for r in baum_rows:
        zaehl[r["portal"]].update(r["stufen"])
        zaehl[r["portal"]]["_n"] += 1
    return {pt: {st for st in STUFEN if c[st] >= min_anteil * c["_n"]} for pt, c in zaehl.items()}


def _kw_zeilen(korr: dict, stimmen: int, meta: dict, baum_rows: list[dict]) -> tuple[list[dict], list[dict], int]:
    sa_all, sf_all, ss_all, sb_all = (abstimmung.lade_stimmen(p) for p in (STIMMEN_KW_ART, STIMMEN_KW_FACH,
                                                                           STIMMEN_KW_STUFEN, STIMMEN_KW_BEDEUTUNG))
    ziele, portale, baum = _fach_ziele(), _fach_portale(), {t["id"]: t for t in lade_roh()}
    meta = {**meta, "methode": KW_METHODE,
            "prompt_hash": _hash(KW_ART_REGELN + KW_FACH_REGELN + STUFEN_REGELN + "".join(PERSPEKTIVEN))}
    portal_stufen = _portal_stufen(baum_rows)
    rows, pruef, ungeeignet, andere_bedeutung = [], [], 0, 0
    for o in _kw_objekte():
        tid, p = o["id"], o["thema"]
        if tid not in sa_all:
            continue
        bedeutung = None
        if nur_synonym(p):
            ba = abstimmung.auswerten(sb_all.get(tid, {}), stimmen, o["text"], max_auswahl=1)
            if not ba["auswahl"] or ba["auswahl"][0] != "gleich":
                if tid not in korr:
                    andere_bedeutung += 1
                    continue
            bedeutung = {"status": ba["status"], "stimmen": {d["code"]: d["stimmen"] for d in ba["details"]}}
        w = {"datensaetze": p["datensaetze"], "stufen": p["stufen"]}
        art, stufen, mapping, gruende = bewerte(tid, o["text"], sa_all[tid], ss_all.get(tid, {}), korr, stimmen, w,
                                                meta)
        if art in (None, "ungeeignet") and mapping["art"]["status"] != "unsicher" and "korrektur" not in mapping:
            ungeeignet += 1
            continue
        if bedeutung:
            mapping["synonym_bedeutung"] = bedeutung
        fa = abstimmung.auswerten(sf_all.get(tid, {}), stimmen, o["text"], max_auswahl=2)
        faecher = [ziele[c].uri for c in fa["auswahl"] if c in ziele]
        mapping["fach"] = {"status": fa["status"], "details": fa["details"], "verworfen": dict(fa["verworfen"])}
        if not faecher:
            faecher = _fach_kandidaten(p)[:1]
            mapping["fach"].update(status="unsicher", ersatz="häufigstes Fach der WLO-Datensätze")
            if "korrektur" not in mapping:
                gruende.append("keine Mehrheit für ein Schulfach")
        if not stufen:
            continue
        pt = portale.get(faecher[0]) or {"portal": ziele[faecher[0].rsplit("/", 1)[-1]].label, "portal_uri": ""}
        erlaubt = portal_stufen.get(pt["portal"]) or FACH_STUFEN.get(pt["portal"])
        if erlaubt and "korrektur" not in mapping and not set(stufen) <= erlaubt:
            mapping["stufen_entfernt"] = [x for x in stufen if x not in erlaubt]
            stufen = [x for x in stufen if x in erlaubt]
            if not stufen:
                ungeeignet += 1
                continue
        # Oberthema im Themenbaum: häufigste Themenbaum-Zuordnung der Datensätze im selben Fachportal
        eltern = next((baum[u] for u, n in sorted(p["themen"].items(), key=lambda x: -x[1])
                       if u in baum and baum[u]["portal"] == pt["portal"]
                       and n >= max(2, KW_ELTERN_ANTEIL * p["datensaetze"])), None)
        row = {"id": tid, "label_de": p["label_de"], "label_gnd": p["label"], "portal": pt["portal"],
               "portal_uri": pt["portal_uri"], "pfad": [*eltern["pfad"], eltern["label_de"]] if eltern else [],
               "ebene": eltern["ebene"] + 1 if eltern else 1, "definition": p["definition"],
               "keywords": list(dict.fromkeys(p["synonyme"] + p["keywords"]))[:10], "faecher": faecher,
               "art": art if art in ("unterrichtsthema", "lehrkraftthema") else "unterrichtsthema",
               "stufen": stufen, "herkunft": "wlo_keyword", "quelle": GND_QUELLE,
               "gnd": {"uri": p["uri"], "label": p["label"]}, "oberbegriffe": p["oberbegriffe"],
               "mapping": mapping,
               "belege": {"wlo": {"quelle": KW_QUELLE, "datensaetze": p["datensaetze"], "stufen": p["stufen"],
                                  "faecher": p["faecher"], "keywords": p["keywords"], "nodes": p["nodes"]}}}
        if eltern:
            row["eltern"] = {"uri": eltern["id"], "label": eltern["label_de"]}
        rows.append(row)
        if gruende:
            pruef.append({"id": tid, "label_de": p["label_de"], "portal": pt["portal"], "pfad": " > ".join(row["pfad"]),
                          "grund": "; ".join(gruende), "art": f"{row['art']} ({mapping['art']['stimmen']})",
                          "stufen": " | ".join(f"{s['id']} ({s['stimmen']}/{stimmen})" for s in mapping["stufen"])
                          or " | ".join(stufen) + " (WLO)",
                          "wlo_stufen": json.dumps(p["stufen"], ensure_ascii=False)})
    return rows, pruef, ungeeignet + andere_bedeutung


# --- 7. Zusammenführen -----------------------------------------------------------------------------

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
    wlo, gnd_map, kw_belege = lade_wlo(), lade_gnd(), lade_kw_baum_belege()
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
               "stufen": stufen, "herkunft": "themenbaum", "quelle": QUELLE, "mapping": mapping}
        if tid in gnd_map:
            row["gnd"] = gnd_map[tid]
        if wlo.get(tid):
            row["belege"] = {"wlo": {"quelle": WLO_QUELLE, **wlo[tid]}}
        if kw_belege.get(tid):
            row.setdefault("belege", {})["wlo_keyword"] = {"quelle": KW_QUELLE, **kw_belege[tid]}
        rows.append(row)
        if gruende:
            pruef.append({"id": tid, "label_de": t["label_de"], "portal": t["portal"], "pfad": " > ".join(t["pfad"]),
                          "grund": "; ".join(gruende), "art": f"{row['art']} ({mapping['art']['stimmen']})",
                          "stufen": " | ".join(f"{s['id']} ({s['stimmen']}/{stimmen})" for s in mapping["stufen"])
                          or " | ".join(stufen) + " (WLO)",
                          "wlo_stufen": json.dumps((wlo.get(tid) or {}).get("stufen", {}), ensure_ascii=False)})
    n_baum = len(rows)
    kw_rows, kw_pruef, kw_ungeeignet = _kw_zeilen(korr, stimmen, meta, rows) if KW_POOL_PATH.exists()         else ([], [], 0)
    rows += kw_rows
    pruef += kw_pruef
    _write_jsonl(THEMEN_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "korrektur_art", "kommentar"])
    return fertig(f"{n_baum} Themenbaum-Themen ({ungeeignet} Sammelknoten verworfen), {len(kw_rows)} Themen aus "
                  f"WLO-Keywords ({kw_ungeeignet} ungeeignet), {len(pruef)} auf der Prüfliste")


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
        "herkunft": dict(Counter(r.get("herkunft", "themenbaum") for r in rows)),
        "status_fach_keywords": dict(Counter((x.get("fach") or {}).get("status") for x, r in zip(m, rows)
                                             if r.get("herkunft") == "wlo_keyword")),
        "keywords_mit_oberthema": sum(1 for r in rows if r.get("eltern")),
    }
