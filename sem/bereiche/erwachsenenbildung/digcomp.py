"""DigComp 3.0 als Themenquelle der Erwachsenenbildung (und optional der Berufsbildung).

Schritte:
  digcomp              Data Supplement (JSON-LD, --digcomp, Standard: data/lokal/digcomp/*.jsonld) lesen, Errata
                       anwenden → data/mappings/digcomp/digcomp30.jsonl (vorhandene Übersetzungen bleiben erhalten)
  digcomp_glossar      deutsche Fachbegriffe für die 126 Glossarbegriffe (LLM) → data/mappings/digcomp/glossar.json
  digcomp_uebersetzen  Bereiche, Kompetenzen und Lernergebnisse ins Deutsche, mit dem Glossar als Vorgabe (LLM)

Thema = ein Lernergebnis. Fachgebiet der Volkshochschul-Statistik je Kompetenz von Hand (digcomp_fachgebiete.json),
Niveau der Erwachsenenbildung aus dem DigComp-Niveau (ebd.).
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path

from ...llm import LLMClient, LLMConfig, run_tasks
from ...paths import DATA_DIR
from ...quellen import digcomp as D
from ..base import fertig
from . import DIR

CACHE = DATA_DIR / "cache"
FACHGEBIETE = DIR / "digcomp_fachgebiete.json"
UEBERSETZUNG_CACHE = CACHE / "digcomp_uebersetzungen.jsonl"
GLOSSAR_CACHE = CACHE / "digcomp_glossar.jsonl"


def _schreiben(rows: list[dict]):
    D.DIR.mkdir(parents=True, exist_ok=True)
    D.DATEI.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def build_digcomp(cfg=None, digcomp_datei: str | None = None, **_):
    pfad = Path(digcomp_datei) if digcomp_datei else D.standard_datei()
    if not pfad or not pfad.exists():
        return fertig("kein DigComp-Data-Supplement gefunden (data/lokal/digcomp/*.jsonld); Download siehe "
                      "data/README.md, übersprungen")
    d = D.lese_jsonld(pfad)
    alt = {(r["typ"], r["id"]): r for r in D.laden()}
    rows = []
    for r in d["bereiche"] + d["kompetenzen"] + d["lernergebnisse"]:
        a = alt.get((r["typ"], r["id"]), {})
        # Übersetzung behalten, solange der englische Text gleich ist
        if a and all(a.get(k) == r.get(k) for k in ("label_en", "beschreibung_en", "text_en")):
            r.update({k: v for k, v in a.items() if k.endswith("_de")})
        rows.append(r)
    _schreiben(rows)
    (D.DIR / "glossar_en.json").write_text(json.dumps(d["glossar"], ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(d["lernergebnisse"])
    return fertig(f"{len(d['bereiche'])} Bereiche, {len(d['kompetenzen'])} Kompetenzen, {n} Lernergebnisse "
                  f"(Errata vom {D.ERRATA_STAND} angewendet) aus {pfad.name}")


GLOSSAR_SYSTEM = """Du legst die deutschen Fachbegriffe für eine Übersetzung des europäischen Kompetenzrahmens DigComp 3.0 fest.
Zu jedem englischen Begriff (mit Erklärung) lieferst du den in Deutschland üblichen Fachbegriff, wie er in Bildung, Verwaltung und IT verwendet wird (z. B. "AI system" → "KI-System", "digital content" → "digitale Inhalte"). Englische Begriffe, die im Deutschen üblich sind (z. B. "Phishing", "Cloud"), bleiben englisch.

Antworte ausschließlich mit JSON: {"ergebnisse": [{"en": "...", "de": "..."}]}"""


def build_digcomp_glossar(cfg: LLMConfig, on_progress=None, stop_event=None, **_):
    pfad = D.DIR / "glossar_en.json"
    if not pfad.exists():
        return fertig("glossar_en.json fehlt: erst den Schritt „digcomp“ ausführen, übersprungen")
    begriffe = json.loads(pfad.read_text(encoding="utf-8"))
    tasks = [{"id": f"glossar:{i}", "begriffe": begriffe[i:i + 40]} for i in range(0, len(begriffe), 40)]

    def worker(client: LLMClient, task: dict) -> dict:
        res = client.chat_json(GLOSSAR_SYSTEM, json.dumps([{"en": b["en"], "erklaerung": b["erklaerung_en"]}
                                                            for b in task["begriffe"]], ensure_ascii=False))
        by = {str(r.get("en")): str(r.get("de", "")).strip() for r in res.get("ergebnisse", []) if isinstance(r, dict)}
        out = {b["en"]: by[b["en"]] for b in task["begriffe"] if by.get(b["en"])}
        if len(out) * 2 < len(task["begriffe"]):
            raise ValueError("zu wenige Begriffe in der Antwort")
        return {"id": task["id"], "begriffe": out}

    prog = run_tasks(tasks, worker, GLOSSAR_CACHE, cfg, on_progress=on_progress, stop_event=stop_event)
    if GLOSSAR_CACHE.exists():
        de = {}
        for z in GLOSSAR_CACHE.read_text(encoding="utf-8").splitlines():
            if z.strip():
                de.update(json.loads(z)["begriffe"])
        glossar = [{"en": b["en"], "de": de.get(b["en"], "")} for b in begriffe]
        D.GLOSSAR.write_text(json.dumps({"hinweis": D.UEBERSETZUNG_HINWEIS, "modell": cfg.model,
                                         "datum": date.today().isoformat(), "begriffe": glossar},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return prog


UEBERSETZEN_SYSTEM = """Du übersetzt den europäischen Kompetenzrahmen DigComp 3.0 ins Deutsche (eigene Übersetzung für ein Bildungsdatenset).
- Übersetze genau und vollständig, in klarem Deutsch, wie es in Lehrplänen und Weiterbildungsprogrammen steht. Nichts weglassen, nichts hinzufügen.
- Lernergebnisse sind Handlungsbeschreibungen; im Deutschen steht das Verb im Infinitiv am Satzende, z. B. "Identify the main features of …" → "Die wichtigsten Merkmale von … erkennen".
- Verwende die Fachbegriffe aus dem Glossar genau so, wie sie dort stehen.
- "AI" heißt "KI"; "digital technologies" heißt "digitale Technologien"; "digital environments" heißt "digitale Umgebungen".

Antworte ausschließlich mit JSON: {"ergebnisse": [{"id": "...", "de": "...", "beschreibung_de": "... (nur wenn eine Beschreibung gegeben ist)"}]}"""


@cache
def _glossar_text() -> str:
    if not D.GLOSSAR.exists():
        return ""
    g = json.loads(D.GLOSSAR.read_text(encoding="utf-8"))["begriffe"]
    return "\n\nGlossar (englisch → deutsch):\n" + "\n".join(f"{b['en']} → {b['de']}" for b in g if b["de"])


def _schluessel(r: dict) -> str:
    return f"{r['typ']}:{r['id']}"


def lade_uebersetzungen() -> dict[str, dict]:
    out = {}
    if UEBERSETZUNG_CACHE.exists():
        for z in UEBERSETZUNG_CACHE.read_text(encoding="utf-8").splitlines():
            if z.strip():
                out.update(json.loads(z)["uebersetzungen"])
    return out


def build_digcomp_uebersetzen(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                              batch: int = 20, **_):
    rows = D.laden()
    if not rows:
        return fertig("digcomp30.jsonl fehlt: erst den Schritt „digcomp“ ausführen, übersprungen")
    have = lade_uebersetzungen()
    todo = [r for r in rows if _schluessel(r) not in have and not (r.get("label_de") or r.get("text_de"))]
    todo = todo[:limit] if limit else todo
    tasks = [{"id": "uebersetzen:" + _schluessel(c[0]) + "|" + _schluessel(c[-1]), "rows": c}
             for c in (todo[i:i + batch] for i in range(0, len(todo), batch))]
    system = UEBERSETZEN_SYSTEM + _glossar_text()

    def worker(client: LLMClient, task: dict) -> dict:
        items = []
        for r in task["rows"]:
            it = {"id": _schluessel(r), "art": {"bereich": "Kompetenzbereich", "kompetenz": "Kompetenz",
                                                "lernergebnis": "Lernergebnis"}[r["typ"]],
                  "en": r.get("label_en") or r.get("text_en")}
            if r.get("beschreibung_en"):
                it["beschreibung_en"] = r["beschreibung_en"]
            items.append(it)
        res = client.chat_json(system, json.dumps(items, ensure_ascii=False))
        by = {str(x.get("id")): x for x in res.get("ergebnisse", []) if isinstance(x, dict)}
        out = {}
        for r in task["rows"]:
            x = by.get(_schluessel(r))
            if not x or not str(x.get("de", "")).strip():
                continue
            if r.get("beschreibung_en") and not str(x.get("beschreibung_de", "")).strip():
                continue
            out[_schluessel(r)] = {"de": str(x["de"]).strip(), "beschreibung_de": str(x.get("beschreibung_de") or "").strip()}
        if len(out) * 2 < len(task["rows"]):
            raise ValueError("zu wenige Übersetzungen in der Antwort")
        return {"id": task["id"], "uebersetzungen": out}

    prog = run_tasks(tasks, worker, UEBERSETZUNG_CACHE, cfg, on_progress=on_progress, stop_event=stop_event)
    uebernehmen(cfg.model)
    return prog


def uebernehmen(modell: str | None = None):
    """Übersetzungen aus dem Zwischenspeicher in digcomp30.jsonl schreiben."""
    have = lade_uebersetzungen()
    rows = D.laden()
    for r in rows:
        u = have.get(_schluessel(r))
        if not u:
            continue
        if r["typ"] == "lernergebnis":
            r["text_de"] = u["de"]
        else:
            r["label_de"] = u["de"]
            r["beschreibung_de"] = u["beschreibung_de"]
        r["uebersetzung_de"] = {"hinweis": D.UEBERSETZUNG_HINWEIS, "methode": "LLM mit Glossar",
                                **({"modell": modell} if modell else {})}
    _schreiben(rows)


@cache
def fachgebiete() -> dict:
    """Handmapping Kompetenz → VHS-Fachgebiete und DigComp-Niveau → Niveau der Erwachsenenbildung."""
    return json.loads(FACHGEBIETE.read_text(encoding="utf-8"))


F_BEREICH = "synth:digcomp_bereich"
F_KOMPETENZ = "synth:digcomp_kompetenz"
F_NIVEAU = "synth:digcomp_niveau"
F_ART = "synth:digcomp_art"
F_KI = "synth:digcomp_ki"
METHODE = ("Lernergebnis aus DigComp 3.0 (Errata angewendet, eigene Übersetzung); Fachgebiet der Volkshochschul-"
           "Statistik je Kompetenz von Hand (digcomp_fachgebiete.json)")


def metadaten(lo: dict) -> dict:
    """Seed-Felder synth:digcomp_* zu einem Lernergebnis (Thema mit Feld „digcomp“)."""
    return {F_BEREICH: {"id": lo["bereich"], "label": lo["bereich_label"], "label_en": lo["bereich_label_en"]},
            F_KOMPETENZ: {"id": lo["kompetenz"], "label": lo["kompetenz_label"], "label_en": lo["kompetenz_label_en"]},
            F_NIVEAU: lo["niveau"], F_ART: D.ARTEN.get(lo["art"], lo["art"]), F_KI: D.KI.get(lo["ki"], lo["ki"])}


def lo_info(lo: dict) -> dict:
    """Angaben zu einem Lernergebnis für Themen und Seeds (Bereich, Kompetenz, Niveau, Art, KI-Bezug, Englisch)."""
    k, b = lo["kompetenz_obj"], lo["bereich_obj"]
    return {"id": lo["id"], "id_original": lo["id_original"], "text_de": lo["text_de"], "bereich": lo["bereich"],
            "bereich_label": b.get("label_de") or b.get("label_en", ""), "bereich_label_en": b.get("label_en", ""),
            "kompetenz": lo["kompetenz"], "kompetenz_label": k.get("label_de") or k.get("label_en", ""),
            "kompetenz_label_en": k.get("label_en", ""), "kompetenz_beschreibung": k.get("beschreibung_de", ""),
            "niveau": lo["niveau"], "art": lo["art"], "ki": lo["ki"], "text_en": lo["text_en"],
            **({"text_original": lo["text_original"]} if lo.get("text_original") else {})}


def themen(systematik: dict) -> list[dict]:
    """Lernergebnisse als Themen der Erwachsenenbildung (nur übersetzte)."""
    los = D.lernergebnisse()
    if not los:
        return []
    fg_by = {f["notation"]: f for f in systematik["fachgebiete"]}
    pb_by = {p["notation"]: p for p in systematik["programmbereiche"]}
    hand = fachgebiete()["kompetenzen"]
    out = []
    for lo in los:
        k = lo["kompetenz_obj"]
        fgs = [fg_by[n] for n in hand[lo["kompetenz"]]["fachgebiete"]]
        pb = pb_by[fgs[0]["notation"][0]]
        info = lo_info(lo)
        out.append({
            "id": D.uri(lo["id"]), "label_de": lo["text_de"], "herkunft": "digcomp", "art": "kursthema",
            "definition": k.get("beschreibung_de", ""),
            "keywords": [info["kompetenz_label"], info["bereich_label"]],
            "oberbegriffe": [{"label": info["kompetenz_label"]}],
            "fachgebiet": [{"notation": f["notation"], "uri": f["uri"], "label": f["label"]} for f in fgs],
            "programmbereich": [{"notation": pb["notation"], "uri": pb["uri"], "label": pb["label"]}],
            "quelle": {"name": D.QUELLE["name"], "uri": D.QUELLE["uri"], "lizenz": D.QUELLE["lizenz"],
                       "quellenangabe": D.QUELLE["quellenangabe"]},
            "mapping": {"methode": METHODE, "status": "redaktionell", "uebersetzung": D.UEBERSETZUNG_HINWEIS},
            "digcomp": info,
        })
    return out
