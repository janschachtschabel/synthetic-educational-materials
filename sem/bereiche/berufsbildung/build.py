"""Baut die Berufsbildungs-Mappings unter data/mappings/berufsbildung/ nach.

Schritte (einzeln oder als Folge aufrufbar, siehe METHODIK.md):
  esco             ESCO-Berufe und ihre essenziellen und optionalen Kompetenzen laden (API, Zwischenspeicher)
  hinweise         BA-Umsteigeschlüssel KldB 2010 (Fassung 2020) ↔ ISCO-08: je ISCO-Gruppe die
                   KldB-Berufsuntergruppen und Anforderungsniveaus (Handmapping-Hinweise)
  kandidaten       je Beruf die ähnlichsten KldB-Berufsuntergruppen (lokale Embeddings) plus die Hinweise
  kldb             drei Stimmen je Beruf: KldB-Berufsuntergruppe(n), mit Begründung und Belegbegriffen (LLM)
  qualifizierung   drei Stimmen je Beruf: üblicher Qualifizierungsweg in Deutschland (LLM)
  wlo              Keywords und Berufsgruppen aus WLO-Exporten in beruflichen Kontexten (Export bleibt lokal)
  zusammenfuehren  berufe.jsonl, kompetenzen.jsonl, pruefliste_berufe.csv; korrekturen.csv hat Vorrang
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from ...llm import LLMConfig, Progress
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder, top_k
from ...paths import DATA_DIR
from ...quellen import esco
from ...vocabs import load as load_vocab
from ..base import fertig
from . import DIR, load_jsonl

CACHE = DATA_DIR / "cache"
BERUFE_PATH = DIR / "berufe.jsonl"
KOMPETENZEN_PATH = DIR / "kompetenzen.jsonl"
PRUEFLISTE_PATH = DIR / "pruefliste_berufe.csv"
KORREKTUREN_PATH = DIR / "korrekturen.csv"
HINWEISE_PATH = CACHE / "berufsbildung_hinweise.json"
KANDIDATEN_PATH = CACHE / "berufsbildung_kandidaten.json"
STIMMEN_KLDB = CACHE / "berufsbildung_stimmen_kldb.jsonl"
STIMMEN_QUALI = CACHE / "berufsbildung_stimmen_qualifizierung.jsonl"
WLO_PATH = CACHE / "wlo_keywords_berufsbildung.json"
OEH_ESCO_SKILLS = CACHE / "oeh" / "escoSkills.ttl"
OEH_ESCO_SKILLS_URL = "https://raw.githubusercontent.com/openeduhub/oeh-metadata-vocabs/master/escoSkills.ttl"
UMSTEIGER_PATH = CACHE / "ba" / "Umsteigeschluessel-KLDB2020-ISCO08.xlsx"
UMSTEIGER_URL = ("https://statistik.arbeitsagentur.de/DE/Statischer-Content/Grundlagen/Klassifikationen/"
                 "Klassifikation-der-Berufe/KldB2010-Fassung2020/Arbeitsmittel/Generische-Publikationen/"
                 "Umsteigeschluessel-KLDB2020-ISCO08.xlsx?__blob=publicationFile&v=1")
UMSTEIGER_QUELLE = {"name": "Umsteigeschlüssel KldB 2010 – überarbeitete Fassung 2020 (5-Steller) zu ISCO-08 "
                            "(4-Steller), Statistik der Bundesagentur für Arbeit, Stand 06.08.2021",
                    "uri": "https://statistik.arbeitsagentur.de/DE/Navigation/Grundlagen/Klassifikationen/"
                           "Klassifikation-der-Berufe/KldB2010-Fassung2020/Arbeitsmittel/Arbeitsmittel-Nav.html"}
# 5. Stelle der KldB: Anforderungsniveau
ANFORDERUNG = {"1": "Helfer", "2": "Fachkraft", "3": "Spezialist", "4": "Experte"}
QUALIFIZIERUNG = ["ausbildung", "fortbildung", "studium", "anlernen"]
N_KANDIDATEN = 15
N_STIMMEN = 3


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _uuid(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def kldb_untergruppen(voc) -> list[dict]:
    """KldB-Berufsuntergruppen (4-Steller, unterste Ebene des OEH-Vokabulars)."""
    return sorted((c for c in voc.concepts if c["level"] == 3), key=lambda c: c["notation"])


def kldb_ableiten(voc, uris: list[str]) -> dict:
    """Berufsuntergruppen → Berufsgruppe, Berufshauptgruppe, Berufsbereich (ohne Dubletten)."""
    out = {"berufsuntergruppe": [voc.ref(u) for u in uris]}
    stufe = uris
    for key in ("berufsgruppe", "berufshauptgruppe", "berufsbereich"):
        stufe = list(dict.fromkeys(voc.by_uri[u]["broader"] for u in stufe))
        out[key] = [voc.ref(u) for u in stufe]
    return out


# --- 1. ESCO laden -------------------------------------------------------------------------------

def build_esco(cfg: LLMConfig | None = None, on_progress=None, **_):
    def prog(i, n):
        if on_progress:
            on_progress(Progress(total=n, done=i, status="lädt ESCO"))

    d = esco.lade(on_progress=prog)
    return fertig(f"{len(d['berufe'])} Berufe, {len(d['kompetenzen'])} Kompetenzen im Zwischenspeicher")


def esco_daten() -> dict:
    d = esco.aus_cache()
    if not d["berufe"]:
        raise FileNotFoundError("ESCO fehlt im Zwischenspeicher: erst den Schritt „esco“ ausführen.")
    return d


# --- 2. Hinweise aus dem BA-Umsteigeschlüssel -----------------------------------------------------

def lade_umsteiger() -> list[dict]:
    """Zeilen des Umsteigeschlüssels: kldb5, isco4, eindeutig, schwerpunkt (lädt die Datei bei Bedarf)."""
    import openpyxl

    if not UMSTEIGER_PATH.exists():
        UMSTEIGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(UMSTEIGER_URL, headers={"User-Agent": "Mozilla/5.0"})
        UMSTEIGER_PATH.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    wb = openpyxl.load_workbook(UMSTEIGER_PATH, read_only=True)
    ws = next(w for w in wb.worksheets if "ISCO" in w.title)
    out = []
    for r in ws.iter_rows(values_only=True):
        k5, isco = str(r[0] or "").strip(), str(r[3] or "").strip()
        if re.fullmatch(r"\d{5}", k5) and re.fullmatch(r"\d{4}", isco):
            out.append({"kldb5": k5, "isco": isco, "eindeutig": str(r[6]).strip() == "1",
                        "schwerpunkt": str(r[7]).strip() == "1"})
    return out


def build_hinweise(cfg: LLMConfig | None = None, **_):
    voc = load_vocab("kldb")
    gueltig = {c["notation"] for c in kldb_untergruppen(voc)}
    je_isco: dict[str, dict] = defaultdict(lambda: {"kldb4": Counter(), "anforderung": Counter()})
    for z in lade_umsteiger():
        h = je_isco[z["isco"]]
        if z["kldb5"][:4] in gueltig:
            h["kldb4"][z["kldb5"][:4]] += 1
        h["anforderung"][ANFORDERUNG.get(z["kldb5"][4], "?")] += 1
    out = {isco: {"kldb4": dict(h["kldb4"]), "anforderung": dict(h["anforderung"])} for isco, h in je_isco.items()}
    HINWEISE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HINWEISE_PATH.write_text(json.dumps({"quelle": UMSTEIGER_QUELLE, "isco": out}, ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(out)} ISCO-Gruppen mit KldB-Hinweisen")


def lade_hinweise() -> dict[str, dict]:
    if not HINWEISE_PATH.exists():
        raise FileNotFoundError(f"{HINWEISE_PATH.name} fehlt: erst den Schritt „hinweise“ ausführen.")
    return json.loads(HINWEISE_PATH.read_text(encoding="utf-8"))["isco"]


# --- Objekte für die Zuordnung ---------------------------------------------------------------------

def _objekte(limit: int | None = None) -> list[dict]:
    d = esco_daten()
    hinweise = lade_hinweise() if HINWEISE_PATH.exists() else {}
    out = []
    for b in sorted(d["berufe"].values(), key=lambda b: (b["code"], b["label"]))[:limit]:
        h = hinweise.get(b["isco"], {})
        anf = ", ".join(f"{k} ({n})" for k, n in sorted(h.get("anforderung", {}).items(), key=lambda x: -x[1]))
        prompt = {"beruf": b["label"], "beschreibung": b["beschreibung"][:700],
                  "weitere_bezeichnungen": b["alt_labels"][:5], "isco_08": b["isco"]}
        out.append({"id": b["uri"], "beruf": b, "hinweis_kldb": sorted(h.get("kldb4", {})), "anforderung": anf,
                    "prompt": prompt,
                    "text": ". ".join(x for x in [b["label"], ", ".join(b["alt_labels"][:5]), b["beschreibung"]] if x)})
    return out


# --- 3. Kandidaten ---------------------------------------------------------------------------------

def kldb_vektoren(emb: Embedder):
    voc = load_vocab("kldb")
    ug = kldb_untergruppen(voc)

    def text(c):
        bg = voc.by_uri[c["broader"]]
        return ". ".join(x for x in [f"Berufsuntergruppe {c['label']}", f"Berufsgruppe {bg['label']}",
                                     c.get("definition", ""), "Tätigkeiten: " + c.get("note", "")[:600]] if x)

    return ug, emb.encode([text(c) for c in ug], art="ziel")


def build_kandidaten(cfg: LLMConfig | None = None, limit: int | None = None, embedding_modell: str | None = None,
                     embedder: Embedder | None = None, n_kandidaten: int = N_KANDIDATEN, **_):
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    objekte = _objekte(limit)
    ug, kv = kldb_vektoren(emb)
    ov = emb.encode([o["text"] for o in objekte], art="anfrage")
    idx, sims = top_k(ov, kv, len(ug))
    out = {}
    for o, row_idx, row_sim in zip(objekte, idx, sims):
        ranked = [(ug[i]["notation"], float(s)) for i, s in zip(row_idx, row_sim)]
        wahl = dict(ranked[:n_kandidaten])
        hinweis = set(o["hinweis_kldb"])
        # aus dem Umsteigeschlüssel kommen bis zu vier weitere Untergruppen dazu (die ähnlichsten)
        for n, s in [x for x in ranked if x[0] in hinweis][:4]:
            wahl.setdefault(n, s)
        out[o["id"]] = {"kandidaten": wahl, "handmapping": sorted(hinweis)}
    KANDIDATEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    KANDIDATEN_PATH.write_text(json.dumps({"embedding_modell": emb.model, "berufe": out}, ensure_ascii=False),
                               encoding="utf-8")
    return fertig(f"{len(out)} Berufe, Modell {emb.model}")


def load_kandidaten() -> dict:
    if not KANDIDATEN_PATH.exists():
        raise FileNotFoundError(f"{KANDIDATEN_PATH.name} fehlt: erst den Kandidaten-Schritt ausführen.")
    return json.loads(KANDIDATEN_PATH.read_text(encoding="utf-8"))


def _systeme(perspektiven: list[str], regeln: str, n: int) -> list[str]:
    return [perspektiven[i % len(perspektiven)] + "\n\n" + regeln for i in range(n)]


# --- 4. KldB abstimmen -----------------------------------------------------------------------------

KLDB_REGELN = """Zu jedem Beruf (ESCO, mit ISCO-08-Code) gibt es eine Liste von Kandidaten (Kennziffer | KldB-Berufsuntergruppe | Berufsgruppe).
- Wähle 1 bis 2 Berufsuntergruppen aus den Kandidaten, die passendste zuerst. Gib als "code" die Kennziffer an.
- Entscheidend ist die berufsfachliche Tätigkeit (was die Person tut und können muss), nicht die Branche des Arbeitgebers.
- Aufsichts- und Führungskräfte gehören in die Untergruppe für Aufsicht bzw. Führung ihres Fachgebiets, wenn es sie unter den Kandidaten gibt.
- Lieber eine Untergruppe zu wenig als eine zu viel. Passt kein Kandidat, gib eine leere Auswahl.
- Je Untergruppe: "begruendung" (ein kurzer Satz) und "belegbegriffe": 1 bis 3 Begriffe wörtlich aus Bezeichnung oder Beschreibung des Berufs."""

KLDB_PERSPEKTIVEN = [
    "Du ordnest Berufe der Klassifikation der Berufe 2010 (KldB) der Bundesagentur für Arbeit zu.",
    "Du bist Berufsberaterin der Bundesagentur für Arbeit und kennst die Berufsfelder der KldB.",
    "Du codierst Berufsangaben für die amtliche Arbeitsmarktstatistik nach der KldB 2010.",
]


def _kldb_ziele(voc) -> dict[str, Ziel]:
    return {c["notation"]: Ziel(c["notation"], c["uri"], c["label"], "Berufsgruppe " + voc.by_uri[c["broader"]]["label"])
            for c in kldb_untergruppen(voc)}


def build_kldb(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
               stimmen: int = N_STIMMEN, **_):
    voc = load_vocab("kldb")
    kand = load_kandidaten()["berufe"]
    objekte = [o for o in _objekte(limit) if o["id"] in kand]
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]["kandidaten"]) for o in objekte},
                                _kldb_ziele(voc), _systeme(KLDB_PERSPEKTIVEN, KLDB_REGELN, stimmen), STIMMEN_KLDB,
                                cfg, max_auswahl=2, batch=10, on_progress=on_progress, stop_event=stop_event)


# --- 5. Qualifizierungsweg abstimmen ---------------------------------------------------------------

QUALI_ZIELE = {
    "ausbildung": Ziel("ausbildung", "ausbildung", "Berufsausbildung",
                       "anerkannter Ausbildungsberuf nach BBiG/HwO oder schulische Berufsausbildung (DQR 3–4)"),
    "fortbildung": Ziel("fortbildung", "fortbildung", "Aufstiegsfortbildung",
                        "setzt eine Ausbildung voraus: Meister, Techniker, Fachwirt, Berufsspezialist (DQR 5–7)"),
    "studium": Ziel("studium", "studium", "Studium", "üblicherweise mit Hochschulabschluss"),
    "anlernen": Ziel("anlernen", "anlernen", "Anlernen", "Helfer- und Anlerntätigkeit ohne formale Berufsausbildung"),
}

QUALI_REGELN = """Ordne jeden Beruf genau einem Qualifizierungsweg zu (code aus den Kandidaten): Auf welchem Weg erlernen Menschen in Deutschland diesen Beruf üblicherweise?
- "ausbildung": dafür gibt es einen anerkannten Ausbildungsberuf (dual oder schulisch), auch wenn die ESCO-Bezeichnung anders lautet.
- "fortbildung": der Beruf setzt eine abgeschlossene Ausbildung und Berufserfahrung voraus und wird über eine Aufstiegsfortbildung erreicht.
- "studium": der Beruf verlangt üblicherweise ein Hochschulstudium.
- "anlernen": Einarbeitung oder kurze Qualifizierung genügt.
"anforderungsniveaus_kldb" nennt die Anforderungsniveaus der KldB-Berufe, die der Umsteigeschlüssel der Bundesagentur für Arbeit derselben ISCO-Gruppe zuordnet (Helfer, Fachkraft, Spezialist, Experte). Das ist nur ein Hinweis: eine ISCO-Gruppe umfasst oft mehrere Berufe.
Gib eine kurze "begruendung" und 1 bis 3 "belegbegriffe" aus Bezeichnung oder Beschreibung an."""

QUALI_PERSPEKTIVEN = [
    "Du kennst das deutsche Berufsbildungssystem (BBiG, HwO, Fortbildungsordnungen, Hochschulen).",
    "Du bist Ausbildungsberaterin einer Industrie- und Handelskammer.",
    "Du bist Berufsberater der Bundesagentur für Arbeit.",
]


def build_qualifizierung(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                         stimmen: int = N_STIMMEN, **_):
    objekte = _objekte(limit)
    for o in objekte:
        o["prompt"] = {**o["prompt"], "anforderungsniveaus_kldb": o["anforderung"] or "unbekannt"}
    alle = list(QUALI_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, QUALI_ZIELE,
                                _systeme(QUALI_PERSPEKTIVEN, QUALI_REGELN, stimmen), STIMMEN_QUALI, cfg,
                                max_auswahl=1, batch=15, on_progress=on_progress, stop_event=stop_event)


# --- 6. WLO-Keywords in beruflichen Kontexten ------------------------------------------------------

WLO_KONTEXTE = {"Berufliche Bildung", "Fortbildung"}
WLO_QUELLE = {"name": "WirLernenOnline", "uri": "https://wirlernenonline.de",
              "hinweis": "nur Keywords und Berufsgruppen (KldB) von Datensätzen der Bildungsstufen Berufliche Bildung "
                         "und Fortbildung genutzt; Beleg als Zahl der Datensätze und Node-IDs"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def lese_wlo(paths: list[Path], delimiter: str = ";") -> dict:
    """keyword (normalisiert) -> {"nodes": set, "kldb": Counter(Kennziffer)}. Gelesen werden nur Node-ID,
    Bildungsstufe, Berufsgruppe und Keywords."""
    from ...quellen.wlo import _liste

    csv.field_size_limit(10 ** 9)
    out: dict = defaultdict(lambda: {"nodes": set(), "kldb": Counter()})
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f, delimiter=delimiter)

            def spalte(name):
                return next((c for c in r.fieldnames if c == name or c.endswith("." + name)), None)

            c_node, c_ctx = spalte("sys:node-uuid"), spalte("ccm:educationalcontext_DISPLAYNAME")
            c_kw, c_pg = spalte("cclom:general_keyword"), spalte("ccm:oeh_profession_group")
            if not c_kw or not c_ctx:
                raise KeyError("Spalten für Keywords oder Bildungsstufe fehlen im Export")
            for i, row in enumerate(r):
                if not WLO_KONTEXTE & set(_liste(row, c_ctx)):
                    continue
                node = row[c_node].strip() if c_node else f"#{Path(path).name}:{i}"
                kldb = [_uuid(u) for u in _liste(row, c_pg) if "/kldb/" in u]
                for kw in {k.strip() for k in (row.get(c_kw) or "").split(",") if k.strip()}:
                    e = out[_norm(kw)]
                    e["nodes"].add(node)
                    e["kldb"].update(kldb)
    return out


def build_wlo(cfg: LLMConfig | None = None, wlo_dateien: list[str] | None = None, **_):
    if not wlo_dateien:
        return fertig("übersprungen – keine WLO-Exporte angegeben (--wlo)")
    data = lese_wlo([Path(p) for p in wlo_dateien])
    rows = {k: {"anzahl": len(v["nodes"]), "nodes": sorted(n for n in v["nodes"] if not n.startswith("#")),
                "kldb": dict(v["kldb"])} for k, v in data.items()}
    WLO_PATH.parent.mkdir(parents=True, exist_ok=True)
    WLO_PATH.write_text(json.dumps({"quelle": WLO_QUELLE, "keywords": rows}, ensure_ascii=False), encoding="utf-8")
    return fertig(f"{len(rows)} Keywords aus beruflichen Kontexten")


def lade_wlo() -> dict:
    return json.loads(WLO_PATH.read_text(encoding="utf-8"))["keywords"] if WLO_PATH.exists() else {}


# --- 7. Zusammenführen -----------------------------------------------------------------------------

METHODE_KLDB = ("Kandidaten per Embedding-Ähnlichkeit und BA-Umsteigeschlüssel ISCO-08 → KldB, "
                "dann LLM-Mehrfachabstimmung mit Begründung und Belegbegriffen")
METHODE_QUALI = "LLM-Mehrfachabstimmung mit Hinweis auf die KldB-Anforderungsniveaus der ISCO-Gruppe"


def oeh_esco_skills() -> dict[str, str]:
    """ESCO-URI → URI im OEH-Vokabular escoSkills (Abgleich über skos:exactMatch)."""
    if not OEH_ESCO_SKILLS.exists():
        OEH_ESCO_SKILLS.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(OEH_ESCO_SKILLS_URL, OEH_ESCO_SKILLS)
    pat = re.compile(r"^<([^>]+)> a skos:Concept|skos:exactMatch esco:([0-9a-f-]+)")
    out, aktuell = {}, None
    base = "http://w3id.org/openeduhub/vocabs/escoSkills/"
    for line in OEH_ESCO_SKILLS.read_text(encoding="utf-8").splitlines():
        m = pat.search(line.strip())
        if not m:
            continue
        if m.group(1):
            aktuell = base + m.group(1)
        elif aktuell:
            out["http://data.europa.eu/esco/skill/" + m.group(2)] = aktuell
    return out


def bewerte(bid: str, text: str, kand: dict, sk: dict, sq: dict, korr: dict, stimmen: int, voc, by_not: dict,
            meta: dict) -> tuple[list[str], str, dict, list[str]]:
    sims = kand["kandidaten"]
    ka = abstimmung.auswerten(sk, stimmen, text, max_auswahl=2, aehnlichkeit=sims)
    qa = abstimmung.auswerten(sq, stimmen, text, max_auswahl=1)
    codes = ka["auswahl"]
    quali = qa["auswahl"][0] if qa["auswahl"] else "ausbildung"
    hinweis = set(kand.get("handmapping", []))
    mapping = {**meta, "status": ka["status"],
               "konfidenz": ka["details"][0]["konfidenz"] if ka["details"] else 0.0,
               "handmapping_bestaetigt": bool(set(codes) & hinweis), "kandidaten": len(sims),
               "kldb": [{**voc.ref(by_not[d["code"]]), **{k: v for k, v in d.items() if k != "code"}}
                        for d in ka["details"]],
               "verworfen": {voc.by_uri[by_not[c]]["label"]: n for c, n in ka["verworfen"].items()},
               "qualifizierung": {"status": qa["status"],
                                  "konfidenz": qa["details"][0]["konfidenz"] if qa["details"] else 0.0,
                                  "stimmen": {**{q: 0 for q in QUALIFIZIERUNG},
                                              **{d["code"]: d["stimmen"] for d in qa["details"]}, **qa["verworfen"]},
                                  "begruendungen": qa["details"][0]["begruendungen"] if qa["details"] else []}}
    if not codes:
        codes = [max(sims, key=sims.get)]
        mapping.update({"status": "unsicher", "konfidenz": 0.0, "handmapping_bestaetigt": codes[0] in hinweis,
                        "ersatz": "keine Untergruppe gewählt; ähnlichste laut Embedding"})
    k = korr.get(bid)
    if k:
        if k["codes"]:
            codes = [c for c in k["codes"] if c in by_not][:2] or codes
            mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
            mapping["handmapping_bestaetigt"] = bool(set(codes) & hinweis)
        if k["lehrebene"] in QUALIFIZIERUNG:
            quali = k["lehrebene"]
            mapping["qualifizierung"]["status"] = "redaktionell"
        mapping["korrektur"] = k
        return codes, quali, mapping, []
    gruende = []
    if mapping.get("ersatz"):
        gruende.append("keine Stimme für eine KldB-Untergruppe, Vorschlag nur aus Embedding")
    elif mapping["status"] == "unsicher":
        gruende.append("keine Mehrheit für eine KldB-Untergruppe")
    if mapping["qualifizierung"]["status"] == "unsicher":
        gruende.append("keine Mehrheit für den Qualifizierungsweg")
    if hinweis and not mapping["handmapping_bestaetigt"] and mapping["status"] == "wahrscheinlich":
        gruende.append("weicht vom BA-Umsteigeschlüssel ab")
    return codes, quali, mapping, gruende


def _meta(cfg, modell, kand_all, stimmen, methode, prompts: str) -> dict:
    return {"methode": methode, "modell": modell or (cfg.model if cfg else None),
            "embedding_modell": kand_all.get("embedding_modell"), "stimmen": stimmen,
            "datum": date.today().isoformat(), "prompt_hash": _hash(prompts)}


def _wlo_beleg(labels: list[str], wlo: dict) -> dict | None:
    """WLO-Keywords, die exakt einer Bezeichnung entsprechen: Datensätze, Node-IDs, Berufsgruppen."""
    nodes, kldb = set(), Counter()
    for l in labels:
        e = wlo.get(_norm(l))
        if e:
            nodes.update(e["nodes"])
            kldb.update(e["kldb"])
    if not nodes and not kldb:
        return None
    return {"datensaetze": len(nodes) or None, "nodes": sorted(nodes)[:10], "kldb": dict(kldb.most_common(10))}


def zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = N_STIMMEN, modell: str | None = None, **_):
    voc = load_vocab("kldb")
    by_not = {c["notation"]: c["uri"] for c in kldb_untergruppen(voc)}
    d = esco_daten()
    kand_all = load_kandidaten()
    kand = kand_all["berufe"]
    sk_all, sq_all = abstimmung.lade_stimmen(STIMMEN_KLDB), abstimmung.lade_stimmen(STIMMEN_QUALI)
    korr = pruefung.lade_korrekturen(KORREKTUREN_PATH, ebene_spalte="korrektur_qualifizierung")
    wlo = lade_wlo()
    alt = {r["uri"]: r for r in load_jsonl(BERUFE_PATH)} if BERUFE_PATH.exists() else {}
    meta = _meta(cfg, modell, kand_all, stimmen, METHODE_KLDB + "; Qualifizierungsweg: " + METHODE_QUALI,
                 "".join(KLDB_PERSPEKTIVEN) + KLDB_REGELN + "".join(QUALI_PERSPEKTIVEN) + QUALI_REGELN)
    rows, pruef = [], []
    neu = behalten = 0
    for o in _objekte():
        bid, b = o["id"], o["beruf"]
        if korr.get(bid, {}).get("ausschliessen"):
            continue
        if bid not in sk_all or bid not in kand:
            if bid in alt:
                rows.append(alt[bid])
                behalten += 1
            continue
        codes, quali, mapping, gruende = bewerte(bid, o["text"], kand[bid], sk_all[bid], sq_all.get(bid, {}), korr,
                                                 stimmen, voc, by_not, meta)
        row = {"uri": bid, "label": b["label"], "alt_labels": b["alt_labels"], "beschreibung": b["beschreibung"],
               "isco": b["isco"], "esco_code": b["code"],
               **kldb_ableiten(voc, [by_not[c] for c in codes]), "qualifizierung": quali,
               "essenziell": [s["uri"] for s in b["essenziell"]], "optional": [s["uri"] for s in b["optional"]],
               "quelle": {"name": "ESCO", "uri": bid, "lizenz": esco.QUELLE["lizenz"]}, "mapping": mapping}
        w = _wlo_beleg([b["label"], *b["alt_labels"]], wlo)
        if w:
            row["belege"] = {"wlo": w}
        rows.append(row)
        neu += 1
        if gruende:
            pruef.append({"id": bid, "label": b["label"], "isco": b["isco"], "grund": "; ".join(gruende),
                          "kldb": " | ".join(f"{x['notation']} {x['label']} ({x['stimmen']}/{stimmen})"
                                             for x in mapping["kldb"])
                          or " | ".join(f"{x['notation']} {x['label']} (Embedding)" for x in row["berufsuntergruppe"]),
                          "verworfen": " | ".join(f"{l} ({n})" for l, n in mapping["verworfen"].items()),
                          "qualifizierung": f"{quali} ({mapping['qualifizierung']['stimmen']})",
                          "umsteigeschluessel": " | ".join(kand[bid].get("handmapping", []))})
    rows.sort(key=lambda r: (r["berufsuntergruppe"][0]["notation"], r["label"]))
    _write_jsonl(BERUFE_PATH, rows)
    pruefung.schreibe_pruefliste(PRUEFLISTE_PATH, pruef, ["korrektur_codes", "korrektur_qualifizierung", "kommentar"])
    n_komp = kompetenzen_schreiben(rows, d["kompetenzen"], wlo)
    return fertig(f"{neu} Berufe neu ausgewertet, {behalten} unverändert übernommen, {len(pruef)} auf der Prüfliste, "
                  f"{n_komp} Kompetenzen")


def kompetenzen_schreiben(berufe: list[dict], esco_komp: dict, wlo: dict) -> int:
    """Alle Kompetenzen, die an einem der Berufe hängen, mit OEH-Abgleich und WLO-Beleg."""
    oeh = oeh_esco_skills()
    ess, opt = Counter(), Counter()
    for b in berufe:
        ess.update(b["essenziell"])
        opt.update(b["optional"])
    rows = []
    for uri in sorted(set(ess) | set(opt)):
        k = esco_komp.get(uri)
        if not k:
            continue
        row = {**k, "essenziell_fuer": ess[uri], "optional_fuer": opt[uri]}
        if uri in oeh:
            row["oeh_uri"] = oeh[uri]
        w = _wlo_beleg([k["label"], *k["alt_labels"]], wlo)
        if w:
            row["belege"] = {"wlo": w}
        rows.append(row)
    _write_jsonl(KOMPETENZEN_PATH, rows)
    return len(rows)


def statistik(berufe: list[dict] | None = None, kompetenzen: list[dict] | None = None) -> dict:
    berufe = berufe if berufe is not None else (load_jsonl(BERUFE_PATH) if BERUFE_PATH.exists() else [])
    kompetenzen = kompetenzen if kompetenzen is not None else (
        load_jsonl(KOMPETENZEN_PATH) if KOMPETENZEN_PATH.exists() else [])
    m = [b.get("mapping", {}) for b in berufe]
    return {
        "berufe": len(berufe),
        "status_kldb": dict(Counter(x.get("status", "ohne Abstimmung") for x in m)),
        "status_qualifizierung": dict(Counter((x.get("qualifizierung") or {}).get("status", "ohne Abstimmung")
                                              for x in m)),
        "qualifizierung": dict(Counter(b.get("qualifizierung") for b in berufe)),
        "handmapping_bestaetigt": sum(1 for x in m if x.get("handmapping_bestaetigt")),
        "kompetenzen": len(kompetenzen),
        "kompetenzen_im_oeh_vokabular": sum(1 for k in kompetenzen if k.get("oeh_uri")),
        "kompetenzen_mit_wlo_beleg": sum(1 for k in kompetenzen if k.get("belege")),
        "beruf_kompetenz_essenziell": sum(len(b["essenziell"]) for b in berufe),
        "beruf_kompetenz_optional": sum(len(b["optional"]) for b in berufe),
    }
