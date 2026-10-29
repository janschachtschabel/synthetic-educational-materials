"""Hochschulthemen aus GND und Bibliothekskatalog, mit WLO-Keywords als zusätzlichem Beleg.

Schritte:
  wlo               Keyword-Fach-Kombinationen aus WLO-Exporten (nur Keywords und Hochschulfächer,
                    Ergebnis im Zwischenspeicher)
  wlo_belege        englische und deutsche WLO-Keywords exakt auf OpenAlex-Themen abgleichen und dort
                    als Beleg vermerken (inkl. Prüfung, ob das WLO-Fach zum abgestimmten Fach passt)
  gnd_sachgruppen   GND-Sachgruppen als Vokabular laden
  bruecke           Studienfach → GND-Sachgruppen (Embedding-Kandidaten, drei Stimmen)
  gnd_pool          Kandidaten: häufige Schlagwörter in Lehrbüchern und Hochschulschriften des
                    hbz-Katalogs sowie WLO-Keywords, die exakt einem GND-Sachschlagwort entsprechen;
                    nur Sachbegriffe (keine Personen, Körperschaften, Orte, Werke, Produkte)
  gnd_relevanz      drei Stimmen: gehört der Begriff als Lehrinhalt an die Hochschule?
  gnd_kandidaten    ähnlichste Studienfächer, plus Hinweise aus WLO-Fächern und der Brücke
  gnd_faecher       drei Stimmen je Begriff
  gnd_lehrebene     drei Stimmen je Begriff, mit Katalogzahlen als Hinweis
  gnd_zusammenfuehren → themen_gnd.jsonl, pruefliste_gnd.csv, gnd_ausgeschlossen.csv
"""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ...llm import LLMConfig, Progress
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder, top_k
from ...paths import VOCAB_DIR
from ...quellen import gnd, wlo
from ...vocabs import load as load_vocab
from ..base import fertig
from . import DIR, clean_label, load_jsonl
from . import build as B

CACHE = B.CACHE
WLO_PATH = CACHE / "wlo_keywords_hochschule.json"
POOL_PATH = CACHE / "hochschule_gnd_pool.jsonl"
STIMMEN_RELEVANZ = CACHE / "hochschule_stimmen_gnd_relevanz.jsonl"
STIMMEN_BRUECKE = CACHE / "hochschule_stimmen_bruecke.jsonl"
KANDIDATEN_GND = CACHE / "hochschule_gnd_kandidaten.json"
STIMMEN_GND_FAECHER = CACHE / "hochschule_stimmen_gnd_faecher.jsonl"
STIMMEN_GND_LEHREBENE = CACHE / "hochschule_stimmen_gnd_lehrebene.jsonl"
BRUECKE_PATH = DIR / "studienfach_gnd_sachgruppe.jsonl"
THEMEN_GND = DIR / "themen_gnd.jsonl"
PRUEF_GND = DIR / "pruefliste_gnd.csv"
AUSGESCHLOSSEN = DIR / "gnd_ausgeschlossen.csv"
LEHRBUCH_FORMEN = {gnd.LEHRBUCH, "https://d-nb.info/gnd/4113937-9"}  # Lehrbuch, Hochschulschrift
PRAEFIXE = [f"{i:03d}" for i in range(1000)]


def _parallel(fn, items, workers=6, on_progress=None, label=""):
    prog = Progress(total=len(items), status=label)
    out = []
    with ThreadPoolExecutor(workers) as pool:
        for r in pool.map(fn, items):
            out.append(r)
            prog.done += 1
            if on_progress and prog.done % 50 == 0:
                on_progress(prog)
    return out


# --- WLO ---------------------------------------------------------------------------------

def build_wlo(cfg=None, wlo_dateien: list[str] | None = None, **_):
    if not wlo_dateien:
        return fertig("kein WLO-Export angegeben, übersprungen")
    wlo.extrahiere([Path(p) for p in wlo_dateien], kontext="Hochschule", out_path=WLO_PATH)
    n = len(json.loads(WLO_PATH.read_text(encoding="utf-8"))["keywords"])
    return fertig(f"{n} Keywords (mindestens 3 Datensätze) im Zwischenspeicher")


def load_wlo() -> dict:
    return json.loads(WLO_PATH.read_text(encoding="utf-8"))["keywords"] if WLO_PATH.exists() else {}


def _wlo_beleg(eintraege: list[dict], voc) -> dict:
    faecher, nodes, anzahl = Counter(), [], 0
    for e in eintraege:
        faecher.update(e["faecher"])
        nodes += e.get("nodes", [])
        anzahl += e["anzahl"]
    top = [{**voc.ref(u), "datensaetze": n} for u, n in faecher.most_common() if u in voc.by_uri][:5]
    out = {"quelle": wlo.QUELLE, "datensaetze": anzahl, "faecher": top}
    if nodes:
        out["nodes"] = [wlo.NODE_URI.format(n) for n in sorted(set(nodes))[:10]]
    return out


def _fach_ebene(row: dict, wlo_uris: list[str], voc) -> str:
    """Feinste Ebene, auf der ein WLO-Fach mit der Zuordnung übereinstimmt (WLO-Fächer stehen oft
    nur auf Ebene Studienbereich oder Fächergruppe)."""
    for ebene in ("studienfach", "studienbereich", "faechergruppe"):
        eigene = {x["uri"] for x in row[ebene]}
        for u in wlo_uris:
            if eigene & ({u} | set(voc.ancestors(u))):
                return ebene
    return "nein"


def build_wlo_belege(cfg=None, **_):
    """WLO-Keywords exakt auf Label und Keywords der OpenAlex-Themen; nur Keywords, die zu höchstens
    drei Themen passen. Prüft, ob ein WLO-Fach im selben Studienbereich liegt wie das abgestimmte Fach."""
    kws = load_wlo()
    if not kws or not B.THEMEN_PATH.exists():
        return fertig("keine WLO-Keywords oder keine OpenAlex-Themen, übersprungen")
    voc = load_vocab("hochschulfaechersystematik")
    rows = load_jsonl(B.THEMEN_PATH)
    index: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        for k in {wlo._norm(x) for x in [r["label_de"], r["label_en"], *r.get("keywords_en", []), *r.get("keywords_de", [])]}:
            index.setdefault(k, []).append(i)
    treffer: dict[int, list[dict]] = {}
    for k, e in kws.items():
        idx = index.get(k, [])
        if 0 < len(idx) <= 3:
            for i in idx:
                treffer.setdefault(i, []).append({**e, "keyword": k})
    passt = 0
    for i, r in enumerate(rows):
        r.setdefault("belege", {}).pop("wlo", None)
        if i not in treffer:
            continue
        b = _wlo_beleg(treffer[i], voc)
        b["keywords"] = sorted({e["keyword"] for e in treffer[i]})[:10]
        b["fach_passt"] = _fach_ebene(r, [f["uri"] for f in b["faecher"]], voc)
        passt += b["fach_passt"] != "nein"
        r["belege"]["wlo"] = b
    for r in rows:
        if not r.get("belege"):
            r.pop("belege", None)
    B._write_jsonl(B.THEMEN_PATH, rows)
    ebenen = Counter(r["belege"]["wlo"]["fach_passt"] for r in rows if r.get("belege", {}).get("wlo"))
    return fertig(f"{len(treffer)} OpenAlex-Themen mit WLO-Beleg, davon {passt} mit passendem Fach "
                  f"({', '.join(f'{k}: {v}' for k, v in ebenen.most_common())})")


# --- Sachgruppen und Brücke ----------------------------------------------------------------

def build_gnd_sachgruppen(cfg=None, **_):
    if (VOCAB_DIR / "gnd_sachgruppen.json").exists():
        return fertig("GND-Sachgruppen schon vorhanden")
    return fertig(f"{gnd.lade_sachgruppen()} GND-Sachgruppen geladen")


def _sachgruppen():
    return [c for c in load_vocab("gnd_sachgruppen").concepts if c["art"] == "sache"]


BRUECKE_REGELN = """Zu jedem Studienfach gibt es Kandidaten aus den GND-Sachgruppen (Notation | Sachgruppe).
- Wähle 1 bis 3 Sachgruppen, unter denen Bibliotheken die Fachliteratur dieses Studienfachs überwiegend erschließen, die passendste zuerst. Gib als "code" die Notation an.
- Passt keine, gib eine leere Auswahl.
- Je Wahl eine kurze "begruendung"; "belegbegriffe" dürfen leer bleiben."""

BRUECKE_PERSPEKTIVEN = ["Du bist Fachreferentin einer Universitätsbibliothek.",
                        "Du bist Sacherschließer bei der Deutschen Nationalbibliothek.",
                        "Du ordnest Studienfächer Bibliothekssystematiken zu."]


def build_bruecke(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = B.N_STIMMEN,
                  embedding_modell: str | None = None, embedder: Embedder | None = None, **_):
    voc = load_vocab("hochschulfaechersystematik")
    sg = _sachgruppen()
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    faecher, fv = B.fach_vektoren(emb)
    gv = emb.encode([f"GND-Sachgruppe {c['label']}" for c in sg], art="ziel")
    idx, _sims = top_k(fv, gv, 12)
    objekte = [{"id": f["uri"], "prompt": {"studienfach": clean_label(f["label"]),
                                            "studienbereich": clean_label(voc.by_uri[f["broader"]]["label"])},
                "text": f["label"]} for f in faecher]
    kand = {o["id"]: [sg[i]["notation"] for i in row] for o, row in zip(objekte, idx)}
    ziele = {c["notation"]: Ziel(c["notation"], c["uri"], c["label"]) for c in sg}
    prog = abstimmung.abstimmen(objekte, kand, ziele, B._systeme(BRUECKE_PERSPEKTIVEN, BRUECKE_REGELN, stimmen),
                                STIMMEN_BRUECKE, cfg, max_auswahl=3, batch=10, on_progress=on_progress,
                                stop_event=stop_event)
    st = abstimmung.lade_stimmen(STIMMEN_BRUECKE)
    rows = []
    for f in faecher:
        if f["uri"] not in st:
            continue
        a = abstimmung.auswerten(st[f["uri"]], stimmen)
        rows.append({"studienfach": voc.ref(f["uri"]), "status": a["status"],
                     "sachgruppen": [{**{k: ziele[d["code"]].__dict__[k] for k in ("uri", "label")},
                                      "notation": d["code"], "stimmen": d["stimmen"], "konfidenz": d["konfidenz"],
                                      "begruendungen": d["begruendungen"]} for d in a["details"]],
                     "mapping": {"methode": "Embedding-Kandidaten, LLM-Mehrfachabstimmung", "modell": cfg.model,
                                 "embedding_modell": emb.model, "stimmen": stimmen}})
    B._write_jsonl(BRUECKE_PATH, rows)
    return prog


def load_bruecke() -> dict[str, list[str]]:
    """Sachgruppen-URI -> Studienfach-URIs (nur Sachgruppen mit Mehrheit)."""
    out: dict[str, list[str]] = {}
    if BRUECKE_PATH.exists():
        for r in load_jsonl(BRUECKE_PATH):
            for s in r["sachgruppen"]:
                if s["stimmen"] >= 2:
                    out.setdefault(s["uri"], []).append(r["studienfach"]["uri"])
    return out


# --- Pool --------------------------------------------------------------------------------

def build_gnd_pool(cfg=None, on_progress=None, limit: int | None = None, min_lehrbuch: int = 3,
                   min_hochschulschrift: int = 10, min_wlo: int = 3, **_):
    """Kandidaten aus Katalog und WLO. Jeder Begriff braucht einen Katalogbeleg (mindestens ein Lehrbuch
    oder drei Hochschulschriften); WLO allein reicht nicht."""
    sg = {c["uri"]: c for c in load_vocab("gnd_sachgruppen").concepts}
    lb, hs = Counter(), Counter()
    for art, ziel in (("lehrbuch", lb), ("hochschulschrift", hs)):
        for res in _parallel(lambda p, a=art: gnd.katalog_top(p, a), PRAEFIXE, on_progress=on_progress,
                             label=f"Katalog {art}"):
            for uri, n in res:
                ziel[uri] += n
    kws = {k: e for k, e in load_wlo().items() if e["anzahl"] >= min_wlo}
    wlo_hits: dict[str, list[dict]] = {}
    for (k, e), rec in zip(kws.items(), _parallel(lambda k: gnd.suche_exakt(next(iter(kws[k]["schreibweisen"]))),
                                                  list(kws), on_progress=on_progress, label="WLO → GND")):
        if rec:
            wlo_hits.setdefault(rec["uri"], []).append({**e, "keyword": k, "_rec": rec})
    uris = [u for u in set(lb) | set(hs) if (lb[u] >= min_lehrbuch or hs[u] >= min_hochschulschrift)]
    recs = gnd.hole([u for u in uris if u not in wlo_hits])
    recs.update({u: hits[0]["_rec"] for u, hits in wlo_hits.items()})

    def zulassen(r):
        if not gnd.ist_thema(r) or r["uri"] in LEHRBUCH_FORMEN or "Motiv>" in (r["label"] or ""):
            return False
        arten = {sg[s["uri"]]["art"] for s in r["sachgruppen"] if s["uri"] in sg}
        return "sache" in arten or not arten

    def wlo_n(r):
        return sum(h["anzahl"] for h in wlo_hits.get(r["uri"], []))

    pool = [r for r in recs.values() if zulassen(r)]
    pool.sort(key=lambda r: -(lb[r["uri"]] + hs[r["uri"]] / 3 + wlo_n(r) / 5))
    if limit:
        pool = pool[:limit * 2]
    zahlen = _parallel(lambda r: gnd.katalog_zaehlen(r["uri"]), pool, on_progress=on_progress, label="Katalog zählen")
    behalten = [(r, z) for r, z in zip(pool, zahlen) if z["lehrbuecher"] >= 1 or z["hochschulschriften"] >= 3]
    if limit:
        behalten = behalten[:limit]
    with POOL_PATH.open("w", encoding="utf-8") as f:
        for r, z in behalten:
            hits = [{k: v for k, v in h.items() if k != "_rec"} for h in wlo_hits.get(r["uri"], [])]
            f.write(json.dumps({"id": r["uri"], "gnd": r, "katalog": z, "wlo": hits}, ensure_ascii=False) + "\n")
    mit_wlo = sum(1 for r, _ in behalten if r["uri"] in wlo_hits)
    return fertig(f"{len(behalten)} GND-Sachbegriffe im Pool, davon {mit_wlo} mit WLO-Beleg "
                  f"({len(pool) - len(behalten)} ohne Katalogbeleg verworfen)")


def load_pool() -> list[dict]:
    if not POOL_PATH.exists():
        raise FileNotFoundError("Erst den Schritt „gnd_pool“ ausführen.")
    return load_jsonl(POOL_PATH)


def _objekt(p: dict, voc=None) -> dict:
    g = p["gnd"]
    ober = [o["label"] for o in g["oberbegriffe"] if o.get("label")]
    prompt = {"label": g["label"], "synonyme": g["synonyme"][:5], "oberbegriffe": ober[:5],
              "gnd_sachgruppen": [s["label"] for s in g["sachgruppen"]], "definition": g.get("definition", ""),
              "in_lehrbuechern": p["katalog"]["lehrbuecher"], "in_hochschulschriften": p["katalog"]["hochschulschriften"]}
    if p.get("wlo") and voc is not None:
        c = Counter()
        for h in p["wlo"]:
            c.update(h["faecher"])
        prompt["faecher_in_lernmaterialien"] = [clean_label(voc.by_uri[u]["label"]) for u, _ in c.most_common(3)
                                                if u in voc.by_uri]
    text = " ".join([g["label"], *g["synonyme"], *ober, g.get("definition", "")])
    return {"id": p["id"], "pool": p, "prompt": prompt, "text": text}


# --- Relevanz ----------------------------------------------------------------------------

RELEVANZ_ZIELE = {
    "hochschule": Ziel("hochschule", "hochschule", "Hochschule", "Gegenstand von Lehrveranstaltungen eines Studiengangs"),
    "schule": Ziel("schule", "schule", "Schule", "praktisch nur im Schulunterricht, an Hochschulen nicht als eigener Inhalt"),
    "berufsbildung": Ziel("berufsbildung", "berufsbildung", "Berufsbildung", "praktische Tätigkeit oder Inhalt einer Berufsausbildung"),
    "allgemein": Ziel("allgemein", "allgemein", "Alltag", "Alltag, Freizeit, Hobby, Lebensführung"),
    "kein_lehrthema": Ziel("kein_lehrthema", "kein_lehrthema", "kein Lehrthema",
                           "Form-, Medien- oder Gattungsbegriff (z. B. Video, Aufsatzsammlung), Einzelobjekt, "
                           "zu allgemein (z. B. Theorie, Methode) oder ohne Lehrbezug"),
}

RELEVANZ_REGELN = """Entscheide je Begriff (GND-Schlagwort mit Oberbegriffen und Zahlen aus dem Katalog der Hochschulbibliotheken), ob er Lehrinhalt an Hochschulen ist. Wähle genau einen code aus den Kandidaten.
- "hochschule", wenn der Begriff als Thema in Vorlesungen, Seminaren, Modulen oder Studienlehrbüchern eines Studiengangs vorkommt. Das gilt auch, wenn er außerdem in der Schule vorkommt (z. B. Chemie, Algorithmus, Grammatik, Globalisierung).
- "schule" nur für Inhalte, die praktisch nur im Schulunterricht behandelt werden (z. B. Einmaleins, Schreibenlernen).
- "berufsbildung" für praktische Tätigkeiten, die vor allem in einer Ausbildung gelernt werden; "allgemein" für Alltag und Freizeit.
- Begriffe, die nur als Form, Medium, Veranstaltungsart oder Gattung dienen (z. B. Video, Vorlesung, Kurs, Anleitung), sind "kein_lehrthema".
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus den Angaben zum Begriff an."""

RELEVANZ_PERSPEKTIVEN = ["Du gestaltest Studiengänge an deutschen Hochschulen.",
                         "Du bist Bildungsforscherin und kennst Lehrpläne von Schule, Ausbildung und Studium.",
                         "Du bist Fachreferent einer Hochschulbibliothek."]


def build_gnd_relevanz(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = B.N_STIMMEN, **_):
    objekte = [_objekt(p) for p in load_pool()]
    alle = list(RELEVANZ_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, RELEVANZ_ZIELE,
                                B._systeme(RELEVANZ_PERSPEKTIVEN, RELEVANZ_REGELN, stimmen), STIMMEN_RELEVANZ, cfg,
                                max_auswahl=1, batch=25, on_progress=on_progress, stop_event=stop_event)


def _relevant(stimmen: int) -> tuple[list[dict], dict]:
    st = abstimmung.lade_stimmen(STIMMEN_RELEVANZ)
    voc = load_vocab("hochschulfaechersystematik")
    rel, bewertung = [], {}
    for p in load_pool():
        if p["id"] not in st:
            continue
        a = abstimmung.auswerten(st[p["id"]], stimmen, max_auswahl=1)
        bewertung[p["id"]] = a
        if a["auswahl"] == ["hochschule"] and a["details"][0]["stimmen"] >= 2:
            rel.append(_objekt(p, voc))
    return rel, bewertung


# --- Fächer und Lehrebene -----------------------------------------------------------------

def build_gnd_kandidaten(cfg=None, stimmen: int = B.N_STIMMEN, embedding_modell: str | None = None,
                         embedder: Embedder | None = None, **_):
    objekte, _ = _relevant(stimmen)
    bruecke = load_bruecke()
    hinweise = {}
    for o in objekte:
        h = set()
        for w in o["pool"]["wlo"]:
            h |= set(w["faecher"])
        for s in o["pool"]["gnd"]["sachgruppen"]:
            h |= set(bruecke.get(s["uri"], []))
        hinweise[o["id"]] = h
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    out = B.kandidaten_berechnen(objekte, emb, hinweise)
    KANDIDATEN_GND.write_text(json.dumps({"embedding_modell": emb.model, "themen": out}, ensure_ascii=False),
                              encoding="utf-8")
    return fertig(f"{len(out)} Hochschul-Begriffe mit Kandidaten")


def build_gnd_faecher(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = B.N_STIMMEN, **_):
    voc = load_vocab("hochschulfaechersystematik")
    kand = B.load_kandidaten(KANDIDATEN_GND)["themen"]
    objekte = [o for o in _relevant(stimmen)[0] if o["id"] in kand]
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]["kandidaten"]) for o in objekte},
                                B._fach_ziele(voc), B._systeme(B.FAECHER_PERSPEKTIVEN, B.FAECHER_REGELN, stimmen),
                                STIMMEN_GND_FAECHER, cfg, max_auswahl=3, batch=10, on_progress=on_progress,
                                stop_event=stop_event)


def build_gnd_lehrebene(cfg: LLMConfig, on_progress=None, stop_event=None, stimmen: int = B.N_STIMMEN, **_):
    objekte = _relevant(stimmen)[0]
    alle = list(B.LEHREBENE_ZIELE)
    regeln = B.LEHREBENE_REGELN + ("\nDie Zahlen in_lehrbuechern und in_hochschulschriften stammen aus dem "
                                   "Bibliothekskatalog und sind ein Hinweis, keine Regel.")
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, B.LEHREBENE_ZIELE,
                                B._systeme(B.LEHREBENE_PERSPEKTIVEN, regeln, stimmen), STIMMEN_GND_LEHREBENE, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- Zusammenführen -----------------------------------------------------------------------

METHODE = ("GND-Sachschlagwort aus Lehrbüchern/Hochschulschriften des hbz-Katalogs oder aus WLO-Keywords "
           "(exakter Abgleich), Hochschulrelevanz, Fach und Lehrebene per LLM-Mehrfachabstimmung")


def build_gnd_zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = B.N_STIMMEN, modell: str | None = None, **_):
    voc = load_vocab("hochschulfaechersystematik")
    by_not = B.faecher_by_not(voc)
    objekte, bewertung = _relevant(stimmen)
    kand_all = B.load_kandidaten(KANDIDATEN_GND)
    kand = kand_all["themen"]
    sf = abstimmung.lade_stimmen(STIMMEN_GND_FAECHER)
    le = abstimmung.lade_stimmen(STIMMEN_GND_LEHREBENE)
    korr = pruefung.lade_korrekturen(B.KORREKTUREN_PATH)
    meta = B._meta(cfg, modell, kand_all, stimmen, METHODE, RELEVANZ_REGELN + "".join(RELEVANZ_PERSPEKTIVEN)
                   + B.FAECHER_REGELN + B.LEHREBENE_REGELN)
    rows, pruef = [], []
    for o in objekte:
        tid = o["id"]
        if tid not in sf or tid not in kand:
            continue
        codes, ebene, mapping, gruende = B.bewerte(tid, o["text"], kand[tid], sf[tid], le.get(tid, {}), korr,
                                                   stimmen, voc, by_not, meta)
        rel = bewertung[tid]
        mapping["hochschulrelevanz"] = {"status": rel["status"], "stimmen": {**{d["code"]: d["stimmen"] for d in rel["details"]},
                                                                             **rel["verworfen"]},
                                        "begruendungen": rel["details"][0]["begruendungen"]}
        p, g = o["pool"], o["pool"]["gnd"]
        belege = {"katalog": {**p["katalog"], "quelle": {"name": "hbz-Verbundkatalog (lobid-resources)",
                                                         "uri": f"https://lobid.org/resources/search?q=subject.componentList.id:%22{tid}%22",
                                                         "lizenz": "CC0-1.0"}}}
        if p["wlo"]:
            belege["wlo"] = _wlo_beleg(p["wlo"], voc)
        rows.append({
            "id": tid, "art": "gnd", "label_de": g["label"], "label_en": "",
            "beschreibung_de": g.get("definition", ""),
            "keywords_de": list(dict.fromkeys(g["synonyme"][:5] + [x["label"] for x in g["oberbegriffe"] if x.get("label")]))[:10],
            **B._derive(voc, [by_not[c] for c in codes]),
            "lehrebene": ebene,
            "gnd": {k: g[k] for k in ("gnd_id", "typen", "sachgruppen", "oberbegriffe")},
            "quelle": {"name": "Gemeinsame Normdatei (GND)", "uri": tid, "lizenz": "CC0-1.0"},
            "belege": belege, "mapping": mapping,
        })
        if rel["status"] != "gesichert":
            gruende.append("Hochschulrelevanz nur mit Mehrheit")
        if gruende:
            pruef.append(B.pruefzeile(rows[-1], gruende, stimmen, lehrbuecher=p["katalog"]["lehrbuecher"],
                                      wlo_datensaetze=belege.get("wlo", {}).get("datensaetze", 0)))
    rows.sort(key=lambda r: (r["studienfach"][0]["notation"], r["label_de"]))
    B._write_jsonl(THEMEN_GND, rows)
    pruefung.schreibe_pruefliste(PRUEF_GND, pruef)
    aus = []
    for p in load_pool():
        a = bewertung.get(p["id"])
        if a and not (a["auswahl"] == ["hochschule"] and a["details"][0]["stimmen"] >= 2):
            aus.append({"id": p["id"], "label": p["gnd"]["label"], "bereich": (a["auswahl"] or ["?"])[0],
                        "stimmen": json.dumps({**{d["code"]: d["stimmen"] for d in a["details"]}, **a["verworfen"]},
                                              ensure_ascii=False),
                        "begruendung": (a["details"][0]["begruendungen"] or [""])[0] if a["details"] else ""})
    pruefung.schreibe_pruefliste(AUSGESCHLOSSEN, aus)
    return fertig(f"{len(rows)} GND-Themen, {len(pruef)} auf der Prüfliste, {len(aus)} nicht Hochschule")
