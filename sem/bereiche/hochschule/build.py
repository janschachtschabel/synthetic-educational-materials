"""Baut die Hochschul-Mappings unter data/mappings/hochschule/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  fachprofile      je Studienfach Kurzbeschreibung und typische Themen (LLM) → studienfach_profile.jsonl
  uebersetzen      OpenAlex-Themen ins Deutsche (LLM; vorhandene Übersetzungen werden übernommen)
  kandidaten       je Thema die ähnlichsten Studienfächer (lokale Embeddings) plus Hinweise aus dem
                   Handmapping Teilgebiet → Studienbereich
  faecher          drei Stimmen je Thema aus den Kandidaten, mit Begründung und Belegbegriffen (LLM)
  lehrebene        drei Stimmen je Thema nach festen Kriterien (LLM)
  zusammenfuehren  Auswertung → themen_openalex.jsonl, pruefliste_themen.csv; korrekturen.csv hat Vorrang

GND-Themen (Grundlagen und weitere Lehrthemen aus GND und Bibliothekskatalog): siehe gnd_themen.py.
"""

from __future__ import annotations

import csv
import hashlib
import json
import urllib.request
from datetime import date
from pathlib import Path

from ...llm import LLMClient, LLMConfig, run_tasks
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder, top_k
from ...paths import DATA_DIR
from ...vocabs import load as load_vocab
from ..base import fertig
from . import DIR, clean_label, load_jsonl

OPENALEX_TOPICS_URL = ("https://raw.githubusercontent.com/ourresearch/openalex-topic-classification/"
                       "main/v2/data/topics/topics.json")
CACHE = DATA_DIR / "cache"
LEHREBENEN = ["grundlagen", "vertiefung", "forschung"]
THEMEN_PATH = DIR / "themen_openalex.jsonl"
PROFILE_PATH = DIR / "studienfach_profile.jsonl"
PRUEFLISTE_PATH = DIR / "pruefliste_themen.csv"
KORREKTUREN_PATH = DIR / "korrekturen.csv"
KANDIDATEN_PATH = CACHE / "hochschule_kandidaten.json"
STIMMEN_FAECHER = CACHE / "hochschule_stimmen_faecher.jsonl"
STIMMEN_LEHREBENE = CACHE / "hochschule_stimmen_lehrebene.jsonl"
UEBERSETZUNG_PATH = CACHE / "hochschule_uebersetzungen.jsonl"
N_KANDIDATEN = 15
N_STIMMEN = 3


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _derive(voc, fach_uris: list[str]) -> dict:
    """Studienfächer → zugehörige Studienbereiche und Fächergruppen (ohne Dubletten, Reihenfolge erhalten)."""
    bereiche = list(dict.fromkeys(voc.by_uri[u]["broader"] for u in fach_uris))
    gruppen = list(dict.fromkeys(voc.by_uri[b]["broader"] for b in bereiche))
    return {"studienfach": [voc.ref(u) for u in fach_uris],
            "studienbereich": [voc.ref(u) for u in bereiche],
            "faechergruppe": [voc.ref(u) for u in gruppen]}


def _faecher(voc) -> list[dict]:
    return sorted((c for c in voc.concepts if c["level"] == 2), key=lambda c: c["notation"])


def _write_jsonl(path: Path, rows: list[dict]):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _topic_num(tid: str) -> int:
    return int(tid.rsplit("T", 1)[-1])


# --- Quellen -------------------------------------------------------------------------------

def load_openalex_topics() -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "openalex_topics.json"
    if not path.exists():
        urllib.request.urlretrieve(OPENALEX_TOPICS_URL, path)
    return json.loads(path.read_text(encoding="utf-8"))


def _topics(limit: int | None = None) -> list[dict]:
    topics = sorted(load_openalex_topics(), key=lambda t: _topic_num(t["id"]))
    return topics[:limit] if limit else topics


def load_subfield_mapping() -> dict[str, list[str]]:
    with (DIR / "openalex_teilgebiet_studienbereich.csv").open(encoding="utf-8") as f:
        return {r["openalex_subfield_id"]: [u.strip() for u in r["studienbereich_uris"].split("|")]
                for r in csv.DictReader(f)}


# --- 1. Fachprofile ------------------------------------------------------------------------

FACHPROFIL_SYSTEM = """Du beschreibst ein Studienfach der Destatis-Fächersystematik, damit Themen ihm automatisch zugeordnet werden können.

Antworte ausschließlich mit JSON:
{"beschreibung_de": "2 Sätze: Gegenstand und typische Inhalte des Fachs an deutschen Hochschulen",
 "typische_themen": ["10-15 typische Lehr- und Forschungsthemen, deutsche Fachbegriffe"],
 "abgrenzung": "1 Satz: welche angrenzenden Themen eher zu welchen Nachbarfächern gehören"}"""


def build_fachprofile(cfg: LLMConfig, on_progress=None, stop_event=None, **_):
    voc = load_vocab("hochschulfaechersystematik")
    raw = CACHE / "hochschule_fachprofile.jsonl"

    def worker(client: LLMClient, task: dict) -> dict:
        f = voc.by_uri[task["id"]]
        sb, fg = voc.by_uri[f["broader"]], voc.by_uri[voc.by_uri[f["broader"]]["broader"]]
        res = client.chat_json(FACHPROFIL_SYSTEM, f"Studienfach: {clean_label(f['label'])}\n"
                               f"Studienbereich: {clean_label(sb['label'])}\nFächergruppe: {fg['label']}")
        if not res.get("beschreibung_de") or not res.get("typische_themen"):
            raise ValueError("unvollständiges Profil")
        return {"id": task["id"], "notation": f["notation"], "label": f["label"],
                "beschreibung_de": res["beschreibung_de"].strip(),
                "typische_themen": [str(t).strip() for t in res["typische_themen"] if str(t).strip()][:15],
                "abgrenzung": str(res.get("abgrenzung", "")).strip(),
                "mapping": {"methode": "LLM-Kurzprofil je Studienfach", "modell": cfg.model,
                            "datum": date.today().isoformat(), "prompt_hash": _hash(FACHPROFIL_SYSTEM)}}

    prog = run_tasks([{"id": f["uri"]} for f in _faecher(voc)], worker, raw, cfg, on_progress=on_progress,
                     stop_event=stop_event)
    if raw.exists():
        rows = {r["id"]: r for r in load_jsonl(raw)}
        _write_jsonl(PROFILE_PATH, sorted(rows.values(), key=lambda r: r["notation"]))
    return prog


def load_profile() -> dict[str, dict]:
    return {r["id"]: r for r in load_jsonl(PROFILE_PATH)} if PROFILE_PATH.exists() else {}


# --- 2. Übersetzen -------------------------------------------------------------------------

UEBERSETZEN_SYSTEM = """Du übersetzt wissenschaftliche Themen (OpenAlex-Topics) für ein deutsches Bildungsdatenset.

Für jedes Thema lieferst du:
- "label_de": prägnanter deutscher Themenname, wie er in einem Vorlesungsverzeichnis stehen könnte
- "beschreibung_de": 1-2 deutsche Sätze, worum es fachlich geht
- "keywords_de": 5-10 deutsche Fachbegriffe

Antworte ausschließlich mit JSON: {"ergebnisse": [{"id": "...", "label_de": "...", "beschreibung_de": "...", "keywords_de": ["..."]}]}"""


def load_uebersetzungen() -> dict[str, dict]:
    """Übersetzungen aus dem Zwischenspeicher, sonst aus der vorhandenen Themendatei."""
    out = {}
    if THEMEN_PATH.exists():
        for r in load_jsonl(THEMEN_PATH):
            if r.get("label_de"):
                out[r["id"]] = {k: r.get(k) for k in ("label_de", "beschreibung_de", "keywords_de")}
    if UEBERSETZUNG_PATH.exists():
        for b in load_jsonl(UEBERSETZUNG_PATH):
            out.update(b["themen"])
    return out


def build_uebersetzen(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None, batch: int = 20, **_):
    have = load_uebersetzungen()
    todo = [t for t in _topics(limit) if "https://openalex.org/" + t["id"] not in have]
    tasks = [{"id": "batch:" + c[0]["id"] + "-" + c[-1]["id"], "topics": c}
             for c in (todo[i:i + batch] for i in range(0, len(todo), batch))]

    def worker(client: LLMClient, task: dict) -> dict:
        items = [{"id": t["id"], "label_en": t["display_name"], "beschreibung_en": t["description"],
                  "keywords_en": t["keywords"]} for t in task["topics"]]
        res = client.chat_json(UEBERSETZEN_SYSTEM, json.dumps(items, ensure_ascii=False))
        by_id = {r.get("id"): r for r in res.get("ergebnisse", [])}
        out = {}
        for t in task["topics"]:
            r = by_id.get(t["id"])
            if not r or not r.get("label_de"):
                raise ValueError(f"unvollständige Antwort für {t['id']}")
            out["https://openalex.org/" + t["id"]] = {
                "label_de": r["label_de"].strip(), "beschreibung_de": (r.get("beschreibung_de") or "").strip(),
                "keywords_de": [k.strip() for k in r.get("keywords_de", []) if k.strip()][:10]}
        return {"id": task["id"], "themen": out}

    return run_tasks(tasks, worker, UEBERSETZUNG_PATH, cfg, on_progress=on_progress, stop_event=stop_event)


# --- Objekte für die Zuordnung ---------------------------------------------------------------

def _objekte(limit: int | None = None) -> list[dict]:
    de = load_uebersetzungen()
    out = []
    for t in _topics(limit):
        tid = "https://openalex.org/" + t["id"]
        d = de.get(tid, {})
        kw = list(dict.fromkeys((d.get("keywords_de") or []) + t["keywords"]))
        out.append({
            "id": tid, "topic": t, "de": d,
            "prompt": {"label_de": d.get("label_de", ""), "label_en": t["display_name"],
                       "beschreibung": d.get("beschreibung_de") or t["description"], "keywords": kw},
            "text": " ".join([d.get("label_de", ""), t["display_name"], d.get("beschreibung_de", ""),
                              t["description"], " ".join(kw)]),
        })
    return out


def _fach_ziele(voc) -> dict[str, Ziel]:
    return {f["notation"]: Ziel(f["notation"], f["uri"], clean_label(f["label"]),
                                "Studienbereich " + clean_label(voc.by_uri[f["broader"]]["label"]))
            for f in _faecher(voc)}


# --- 3. Kandidaten -------------------------------------------------------------------------

def fach_vektoren(emb: Embedder):
    """Embeddings der Studienfächer aus Name, Studienbereich und Fachprofil."""
    voc = load_vocab("hochschulfaechersystematik")
    faecher = _faecher(voc)
    profile = load_profile()

    def text(f):
        sb = voc.by_uri[f["broader"]]
        p = profile.get(f["uri"], {})
        return ". ".join(x for x in [f"Studienfach {clean_label(f['label'])}", f"Studienbereich {clean_label(sb['label'])}",
                                     p.get("beschreibung_de", ""),
                                     "Typische Themen: " + ", ".join(p.get("typische_themen", []))] if x)

    return faecher, emb.encode([text(f) for f in faecher], art="ziel")


def kandidaten_berechnen(objekte: list[dict], emb: Embedder, hinweise: dict[str, set[str]] | None = None,
                         n_kandidaten: int = N_KANDIDATEN) -> dict:
    """Je Objekt die ähnlichsten Studienfächer; aus jedem Hinweis (Studienbereich- oder Studienfach-URI)
    kommen die zwei ähnlichsten passenden Fächer dazu."""
    if not objekte:
        return {}
    faecher, fv = fach_vektoren(emb)
    ov = emb.encode([o["text"] for o in objekte], art="anfrage")
    idx, sims = top_k(ov, fv, len(faecher))
    bereich_von = {f["notation"]: f["broader"] for f in faecher}
    uri_von = {f["notation"]: f["uri"] for f in faecher}
    hinweise = hinweise or {}
    out = {}
    for o, row_idx, row_sim in zip(objekte, idx, sims):
        ranked = [(faecher[i]["notation"], float(s)) for i, s in zip(row_idx, row_sim)]
        wahl = dict(ranked[:n_kandidaten])
        h = hinweise.get(o["id"], set())
        for u in h:
            for n, s in [x for x in ranked if u in (bereich_von[x[0]], uri_von[x[0]])][:2]:
                wahl.setdefault(n, s)
        out[o["id"]] = {"kandidaten": wahl, "handmapping": sorted(h)}
    return out


def build_kandidaten(cfg: LLMConfig | None = None, limit: int | None = None, on_progress=None,
                     embedding_modell: str | None = None, embedder: Embedder | None = None,
                     n_kandidaten: int = N_KANDIDATEN, **_):
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    objekte = _objekte(limit)
    sf_map = load_subfield_mapping()
    hinweise = {o["id"]: set(sf_map.get(o["topic"]["subfield"]["id"], [])) for o in objekte}
    out = kandidaten_berechnen(objekte, emb, hinweise, n_kandidaten)
    KANDIDATEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    KANDIDATEN_PATH.write_text(json.dumps({"embedding_modell": emb.model, "themen": out}, ensure_ascii=False),
                               encoding="utf-8")
    return fertig(f"{len(out)} Themen, Modell {emb.model}")


def faecher_by_not(voc) -> dict[str, str]:
    return {f["notation"]: f["uri"] for f in _faecher(voc)}


def load_kandidaten(path: Path | None = None) -> dict:
    path = path or KANDIDATEN_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path.name} fehlt: erst den Kandidaten-Schritt ausführen.")
    return json.loads(path.read_text(encoding="utf-8"))


# --- 4. Fächer abstimmen --------------------------------------------------------------------

FAECHER_REGELN = """Zu jedem Thema gibt es eine Liste von Kandidaten (Notation | Studienfach | Studienbereich).
- Wähle 1 bis 3 Studienfächer aus den Kandidaten, das passendste zuerst. Gib als "code" die Notation an.
- Wähle nur Fächer, in denen das Thema tatsächlich gelehrt oder erforscht wird. Lieber ein Fach zu wenig als eins zu viel.
- Sprach- oder regionsspezifische Themen gehören in die jeweilige Philologie bzw. Regionalwissenschaft.
- Passt kein Kandidat, gib eine leere Auswahl.
- Je Fach: "begruendung" (ein kurzer Satz) und "belegbegriffe": 1 bis 3 Begriffe wörtlich aus Label, Beschreibung oder Keywords des Themas, die die Zuordnung stützen."""

FAECHER_PERSPEKTIVEN = [
    "Du ordnest wissenschaftliche Themen den Studienfächern der Destatis-Systematik zu.",
    "Du bist Studienberaterin an einer deutschen Hochschule: In welchem Studienfach lernen Studierende dieses Thema?",
    "Du bist Fachreferent einer Universitätsbibliothek: Welchem Studienfach ordnest du Literatur zu diesem Thema zu?",
]


def _systeme(perspektiven: list[str], regeln: str, n: int) -> list[str]:
    return [perspektiven[i % len(perspektiven)] + "\n\n" + regeln for i in range(n)]


def build_faecher(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                  stimmen: int = N_STIMMEN, **_):
    voc = load_vocab("hochschulfaechersystematik")
    kand = load_kandidaten()["themen"]
    objekte = [o for o in _objekte(limit) if o["id"] in kand]
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]["kandidaten"]) for o in objekte},
                                _fach_ziele(voc), _systeme(FAECHER_PERSPEKTIVEN, FAECHER_REGELN, stimmen),
                                STIMMEN_FAECHER, cfg, max_auswahl=3, batch=10, on_progress=on_progress,
                                stop_event=stop_event)


# --- 5. Lehrebene abstimmen -----------------------------------------------------------------

LEHREBENE_ZIELE = {
    "grundlagen": Ziel("grundlagen", "grundlagen", "Grundlagen",
                       "Lehrbuchwissen aus Einführungs- und Pflichtmodulen der ersten Bachelorsemester"),
    "vertiefung": Ziel("vertiefung", "vertiefung", "Vertiefung",
                       "etabliertes Wissen eines Teilgebiets, typisch für Wahlpflichtmodule im späteren Bachelor oder im Master"),
    "forschung": Ziel("forschung", "forschung", "Forschung",
                      "aktuelles Spezialgebiet, das vor allem in Forschungsseminaren, Promotion oder Fachliteratur vorkommt"),
}

LEHREBENE_REGELN = """Ordne jedes Thema genau einer Lehrebene zu (code aus den Kandidaten). Entscheidend ist, wo das Thema in deutschen Studiengängen überwiegend gelehrt wird, nicht wie schwierig es klingt.
- Breite Themen, die in vielen Einführungsvorlesungen vorkommen, sind "grundlagen".
- Benannte Teilgebiete mit eigenen Lehrbüchern und Modulen sind "vertiefung".
- Sehr spezielle Methoden, Stoffe, Krankheitsbilder oder aktuelle Technologien ohne eigenes Lehrmodul sind "forschung".
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus dem Thema an."""

LEHREBENE_PERSPEKTIVEN = [
    "Du planst Modulhandbücher deutscher Studiengänge.",
    "Du bist Studiendekanin und ordnest Themen dem Studienverlauf zu.",
    "Du bist Lehrbuchlektor und schätzt ein, auf welcher Stufe ein Thema gelehrt wird.",
]


def build_lehrebene(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                    stimmen: int = N_STIMMEN, **_):
    objekte = _objekte(limit)
    alle = list(LEHREBENE_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, LEHREBENE_ZIELE,
                                _systeme(LEHREBENE_PERSPEKTIVEN, LEHREBENE_REGELN, stimmen), STIMMEN_LEHREBENE, cfg,
                                max_auswahl=1, batch=20, on_progress=on_progress, stop_event=stop_event)


# --- 6. Zusammenführen ----------------------------------------------------------------------

METHODE = ("Kandidaten per Embedding-Ähnlichkeit und Handmapping Teilgebiet → Studienbereich, "
           "dann LLM-Mehrfachabstimmung mit Begründung und Belegbegriffen")


def bewerte(tid: str, text: str, kand: dict, sf: dict, le: dict, korr: dict, stimmen: int, voc, by_not: dict,
            meta: dict) -> tuple[list[str], str, dict, list[str]]:
    """Wertet Fach- und Lehrebenen-Stimmen eines Themas aus. Liefert Fach-Notationen, Lehrebene,
    mapping-Angaben und Gründe für die Prüfliste."""
    sims = kand["kandidaten"]
    fa = abstimmung.auswerten(sf, stimmen, text, aehnlichkeit=sims)
    lv = abstimmung.auswerten(le, stimmen, text, max_auswahl=1)
    codes = fa["auswahl"]
    ebene = lv["auswahl"][0] if lv["auswahl"] else "vertiefung"
    hinweis = set(kand.get("handmapping", []))

    def bestaetigt(cs):
        return any(voc.by_uri[by_not[c]]["broader"] in hinweis or by_not[c] in hinweis for c in cs)

    mapping = {**meta, "status": fa["status"],
               "konfidenz": fa["details"][0]["konfidenz"] if fa["details"] else 0.0,
               "handmapping_bestaetigt": bestaetigt(codes), "kandidaten": len(sims),
               "faecher": [{**voc.ref(by_not[d["code"]]), **{k: v for k, v in d.items() if k != "code"}}
                           for d in fa["details"]],
               "verworfen": {voc.by_uri[by_not[c]]["label"]: n for c, n in fa["verworfen"].items()},
               "lehrebene": {"status": lv["status"],
                             "konfidenz": lv["details"][0]["konfidenz"] if lv["details"] else 0.0,
                             "stimmen": {**{e: 0 for e in LEHREBENEN}, **{d["code"]: d["stimmen"] for d in lv["details"]},
                                         **lv["verworfen"]},
                             "begruendungen": lv["details"][0]["begruendungen"] if lv["details"] else []}}
    if not codes:
        # keine Stimme für irgendein Fach: Thema behalten, ähnlichstes Fach laut Embedding, zur Prüfung
        codes = [max(sims, key=sims.get)]
        mapping.update({"status": "unsicher", "konfidenz": 0.0, "handmapping_bestaetigt": bestaetigt(codes),
                        "ersatz": "kein Fach gewählt; ähnlichstes Fach laut Embedding"})
    k = korr.get(tid)
    if k:
        if k["codes"]:
            codes = [c for c in k["codes"] if c in by_not][:3] or codes
            mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
        if k["lehrebene"] in LEHREBENEN:
            ebene = k["lehrebene"]
            mapping["lehrebene"]["status"] = "redaktionell"
        mapping["korrektur"] = k
        return codes, ebene, mapping, []
    gruende = []
    if mapping.get("ersatz"):
        gruende.append("keine Stimme für ein Fach, Vorschlag nur aus Embedding")
    elif mapping["status"] == "unsicher":
        gruende.append("keine Mehrheit für ein Fach")
    if mapping["lehrebene"]["status"] == "unsicher":
        gruende.append("keine Mehrheit für die Lehrebene")
    if hinweis and not mapping["handmapping_bestaetigt"] and mapping["status"] == "wahrscheinlich":
        gruende.append("weicht vom Handmapping ab")
    if fa["details"] and not fa["details"][0]["belegbegriffe_im_text"]:
        gruende.append("Hauptfach ohne Beleg im Text")
    return codes, ebene, mapping, gruende


def pruefzeile(row: dict, gruende: list[str], stimmen: int, **extra) -> dict:
    m = row["mapping"]
    return {"id": row["id"], "label_de": row["label_de"], "grund": "; ".join(gruende),
            "faecher": " | ".join(f"{x['notation']} {clean_label(x['label'])} ({x['stimmen']}/{stimmen})"
                                  for x in m["faecher"])
            or " | ".join(f"{f['notation']} {clean_label(f['label'])} (Embedding)" for f in row["studienfach"]),
            "verworfen": " | ".join(f"{l} ({n})" for l, n in m["verworfen"].items()),
            "lehrebene": f"{row['lehrebene']} ({m['lehrebene']['stimmen']})", **extra}


def _meta(cfg, modell, kand_all, stimmen, methode, prompts: str) -> dict:
    return {"methode": methode, "modell": modell or (cfg.model if cfg else None),
            "embedding_modell": kand_all.get("embedding_modell"), "stimmen": stimmen,
            "datum": date.today().isoformat(), "prompt_hash": _hash(prompts)}


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    voc = load_vocab("hochschulfaechersystematik")
    by_not = faecher_by_not(voc)
    kand_all = load_kandidaten()
    kand = kand_all["themen"]
    sf_stimmen = abstimmung.lade_stimmen(STIMMEN_FAECHER)
    le_stimmen = abstimmung.lade_stimmen(STIMMEN_LEHREBENE)
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH)
    alt = {r["id"]: r for r in load_jsonl(THEMEN_PATH)} if THEMEN_PATH.exists() else {}
    meta = _meta(cfg, modell, kand_all, stimmen, METHODE, "".join(FAECHER_PERSPEKTIVEN) + FAECHER_REGELN
                 + "".join(LEHREBENE_PERSPEKTIVEN) + LEHREBENE_REGELN)
    rows, pruef = [], []
    neu = behalten = 0
    for o in _objekte():
        tid, t = o["id"], o["topic"]
        if tid not in sf_stimmen or tid not in kand:
            if tid in alt:
                rows.append(alt[tid])
                behalten += 1
            continue
        codes, ebene, mapping, gruende = bewerte(tid, o["text"], kand[tid], sf_stimmen[tid], le_stimmen.get(tid, {}),
                                                 korr, stimmen, voc, by_not, meta)
        d = o["de"]
        rows.append({
            "id": tid, "art": "openalex",
            "label_de": d.get("label_de") or t["display_name"], "label_en": t["display_name"],
            "beschreibung_de": d.get("beschreibung_de", ""), "beschreibung_en": t["description"],
            "keywords_de": d.get("keywords_de", []), "keywords_en": t["keywords"],
            **_derive(voc, [by_not[c] for c in codes]),
            "lehrebene": ebene,
            "openalex": {x: t[x] for x in ("subfield", "field", "domain")},
            "quelle": {"name": "OpenAlex Topics", "uri": tid, "lizenz": "CC0-1.0"},
            "mapping": mapping,
        })
        if tid in alt and alt[tid].get("belege"):
            rows[-1]["belege"] = alt[tid]["belege"]
        neu += 1
        if gruende:
            pruef.append(pruefzeile(rows[-1], gruende, stimmen, openalex_teilgebiet=t["subfield"]["display_name"]))
    rows.sort(key=lambda r: _topic_num(r["id"]))
    _write_jsonl(THEMEN_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef)
    return fertig(f"{neu} Themen neu ausgewertet, {behalten} unverändert übernommen, {len(pruef)} auf der Prüfliste")


def statistik(rows: list[dict] | None = None) -> dict:
    """Kennzahlen zur Güte des Mappings (für Oberfläche und Doku)."""
    from collections import Counter

    rows = rows if rows is not None else (load_jsonl(THEMEN_PATH) if THEMEN_PATH.exists() else [])
    m = [r.get("mapping", {}) for r in rows]
    return {
        "themen": len(rows),
        "status_fach": dict(Counter(x.get("status", "ohne Abstimmung") for x in m)),
        "status_lehrebene": dict(Counter((x.get("lehrebene") or {}).get("status", "ohne Abstimmung")
                                         if isinstance(x.get("lehrebene"), dict) else "ohne Abstimmung" for x in m)),
        "lehrebene": dict(Counter(r.get("lehrebene") for r in rows)),
        "handmapping_bestaetigt": sum(1 for x in m if x.get("handmapping_bestaetigt")),
        "faecher_je_thema": dict(Counter(len(r["studienfach"]) for r in rows)),
    }
