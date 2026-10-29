"""Baut die Hochschul-Mappings unter data/mappings/hochschule/ nach.

Schritte (einzeln aufrufbar, siehe METHODIK.md):
  openalex     OpenAlex Topics laden, über das Handmapping Teilgebiet → Studienbereich vorsortieren
               und per LLM auf Studienfächer feinmappen (deutsch: Label, Beschreibung, Keywords,
               Lehrebene)            → themen_openalex.jsonl
  grundlagen   je Studienfach Grundlagenthemen per LLM vorschlagen und über die GND belegen
                                     → themen_grundlagen.jsonl (+ grundlagen_ohne_gnd.csv)
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from ...llm import LLMClient, LLMConfig, run_tasks
from ...paths import DATA_DIR
from ...vocabs import load as load_vocab
from . import DIR, clean_label, load_jsonl

OPENALEX_TOPICS_URL = ("https://raw.githubusercontent.com/ourresearch/openalex-topic-classification/"
                       "main/v2/data/topics/topics.json")
CACHE = DATA_DIR / "cache"
LEHREBENEN = ["grundlagen", "vertiefung", "forschung"]


def _ref(voc, uri):
    return voc.ref(uri)


def _derive(voc, fach_uris: list[str]) -> dict:
    """Studienfächer → zugehörige Studienbereiche und Fächergruppen (ohne Dubletten, Reihenfolge erhalten)."""
    bereiche = list(dict.fromkeys(voc.by_uri[u]["broader"] for u in fach_uris))
    gruppen = list(dict.fromkeys(voc.by_uri[b]["broader"] for b in bereiche))
    return {"studienfach": [_ref(voc, u) for u in fach_uris],
            "studienbereich": [_ref(voc, u) for u in bereiche],
            "faechergruppe": [_ref(voc, u) for u in gruppen]}


def _fachliste(voc) -> tuple[str, dict]:
    faecher = [c for c in voc.concepts if c["level"] == 2]
    by_not = {f["notation"]: f for f in faecher}
    lines = []
    for f in sorted(faecher, key=lambda f: (voc.by_uri[f["broader"]]["notation"], f["notation"])):
        sb = voc.by_uri[f["broader"]]
        lines.append(f'{f["notation"]} | {clean_label(f["label"])} | Studienbereich {sb["notation"]} {clean_label(sb["label"])}')
    return "\n".join(lines), by_not


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# --- OpenAlex ------------------------------------------------------------------------------

def load_openalex_topics() -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "openalex_topics.json"
    if not path.exists():
        urllib.request.urlretrieve(OPENALEX_TOPICS_URL, path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_subfield_mapping() -> dict[str, list[str]]:
    with (DIR / "openalex_teilgebiet_studienbereich.csv").open(encoding="utf-8") as f:
        return {r["openalex_subfield_id"]: [u.strip() for u in r["studienbereich_uris"].split("|")]
                for r in csv.DictReader(f)}


OPENALEX_SYSTEM = """Du ordnest wissenschaftliche Themen (OpenAlex-Topics) der Destatis-Systematik der Studienfächer zu und übersetzt sie für ein deutsches Bildungsdatenset.

Für jedes Thema lieferst du:
- "label_de": prägnanter deutscher Themenname, wie er in einem Vorlesungsverzeichnis stehen könnte
- "beschreibung_de": 1-2 deutsche Sätze, worum es fachlich geht
- "keywords_de": 5-10 deutsche Fachbegriffe
- "studienfaecher": 1-3 Notationen aus der Liste unten, das passendste zuerst. Nur Fächer, in denen das Thema tatsächlich gelehrt wird. Sprach- oder regionsspezifische Themen gehören in die jeweilige Philologie bzw. Regionalwissenschaft.
- "lehrebene": "grundlagen" (Bachelor-Grundstudium), "vertiefung" (fortgeschrittener Bachelor/Master) oder "forschung" (Promotion/Spezialforschung)

"studienbereich_hinweis" stammt aus einem groben Mapping über das OpenAlex-Teilgebiet. Übernimm ihn, wenn er passt, sonst wähle frei.

Antworte ausschließlich mit JSON: {"ergebnisse": [{"id": "...", "label_de": "...", "beschreibung_de": "...", "keywords_de": [...], "studienfaecher": ["..."], "lehrebene": "..."}]}

Studienfächer (Notation | Studienfach | Studienbereich):
"""


def build_openalex(cfg: LLMConfig, batch: int = 20, limit: int | None = None, on_progress=None, stop_event=None):
    voc = load_vocab("hochschulfaechersystematik")
    fachliste, by_not = _fachliste(voc)
    system = OPENALEX_SYSTEM + fachliste
    sf_map = load_subfield_mapping()
    topics = sorted(load_openalex_topics(), key=lambda t: (t["subfield"]["id"], t["id"]))
    if limit:
        topics = topics[:limit]
    tasks = []
    for i in range(0, len(topics), batch):
        chunk = topics[i:i + batch]
        tasks.append({"id": "batch:" + chunk[0]["id"] + "-" + chunk[-1]["id"], "topics": chunk})

    meta = {"modell": cfg.model, "datum": date.today().isoformat(), "prompt_hash": _hash(system)}
    out_path = DIR / "themen_openalex.jsonl"
    done = {r["id"] for r in load_jsonl(out_path)} if out_path.exists() else set()
    tasks = [t for t in tasks if any("https://openalex.org/" + x["id"] not in done for x in t["topics"])]

    # run_tasks schreibt pro Aufgabe eine Zeile; ein Batch liefert mehrere Themen. Deshalb sammelt
    # der Worker die Themen in einer Batch-Zeile, die danach in Einzelzeilen aufgelöst wird.
    raw_path = CACHE / "themen_openalex.batches.jsonl"

    def worker(client: LLMClient, task: dict) -> dict:
        items = [{"id": t["id"], "label_en": t["display_name"], "beschreibung_en": t["description"],
                  "keywords_en": t["keywords"], "openalex_subfield": t["subfield"]["display_name"],
                  "studienbereich_hinweis": [clean_label(voc.by_uri[u]["label"]) for u in sf_map[t["subfield"]["id"]]]}
                 for t in task["topics"]]
        res = client.chat_json(system, json.dumps(items, ensure_ascii=False))
        by_id = {r.get("id"): r for r in res.get("ergebnisse", [])}
        rows = []
        for t in task["topics"]:
            r = by_id.get(t["id"])
            nots = [str(n).strip() for n in (r or {}).get("studienfaecher", []) if str(n).strip() in by_not][:3]
            if not r or not nots or not r.get("label_de"):
                raise ValueError(f"unvollständige Antwort für {t['id']}")
            rows.append({
                "id": "https://openalex.org/" + t["id"], "art": "openalex",
                "label_de": r["label_de"].strip(), "label_en": t["display_name"],
                "beschreibung_de": (r.get("beschreibung_de") or "").strip(), "beschreibung_en": t["description"],
                "keywords_de": [k.strip() for k in r.get("keywords_de", []) if k.strip()][:10],
                "keywords_en": t["keywords"],
                **_derive(voc, [by_not[n]["uri"] for n in nots]),
                "lehrebene": r.get("lehrebene") if r.get("lehrebene") in LEHREBENEN else "vertiefung",
                "openalex": {k: t[k] for k in ("subfield", "field", "domain")},
                "quelle": {"name": "OpenAlex Topics", "uri": "https://openalex.org/" + t["id"], "lizenz": "CC0-1.0"},
                "mapping": {"methode": "OpenAlex-Teilgebiet → Studienbereich (Handmapping), dann LLM-Feinmapping auf Studienfach",
                            **meta},
            })
        return {"id": task["id"], "themen": rows}

    prog = run_tasks(tasks, worker, raw_path, cfg, on_progress=on_progress, stop_event=stop_event)
    _flatten_batches(raw_path, out_path)
    return prog


def _flatten_batches(raw_path: Path, out_path: Path):
    if not raw_path.exists():
        return
    rows = {r["id"]: r for r in load_jsonl(out_path)} if out_path.exists() else {}
    for b in load_jsonl(raw_path):
        for r in b["themen"]:
            rows[r["id"]] = r
    out_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in sorted(rows.values(), key=lambda r: int(r["id"].rsplit("T", 1)[-1]))),
                        encoding="utf-8")


def import_topic_mapping(path: Path, model: str = "gpt-6-luna", datum: str | None = None):
    """Übernimmt ein bereits erzeugtes Feinmapping (Format von topics_hochschule_llm.jsonl)."""
    voc = load_vocab("hochschulfaechersystematik")
    topics = {"https://openalex.org/" + t["id"]: t for t in load_openalex_topics()}
    rows = []
    for r in load_jsonl(Path(path)):
        t = topics[r["id"]]
        rows.append({
            "id": r["id"], "art": "openalex", "label_de": r["label_de"], "label_en": t["display_name"],
            "beschreibung_de": r.get("beschreibung_de", ""), "beschreibung_en": t["description"],
            "keywords_de": r.get("keywords_de", []), "keywords_en": t["keywords"],
            **_derive(voc, [f["uri"] for f in r["studienfach"]]),
            "lehrebene": r.get("lehrebene") or "vertiefung",
            "openalex": {k: t[k] for k in ("subfield", "field", "domain")},
            "quelle": {"name": "OpenAlex Topics", "uri": r["id"], "lizenz": "CC0-1.0"},
            "mapping": {"methode": "OpenAlex-Teilgebiet → Studienbereich (Handmapping), dann LLM-Feinmapping auf Studienfach",
                        "modell": r.get("mapping_modell", model), "datum": datum or date.today().isoformat()},
        })
    rows.sort(key=lambda r: int(r["id"].rsplit("T", 1)[-1]))
    (DIR / "themen_openalex.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                               encoding="utf-8")
    return len(rows)


# --- Grundlagen über GND -------------------------------------------------------------------

GRUNDLAGEN_SYSTEM = """Du planst ein Bachelorstudium. Für ein Studienfach nennst du die Grundlagenthemen, die in den ersten Semestern typischerweise gelehrt werden: Einführungs- und Grundlagenmodule, zentrale Methoden, Basiskonzepte.

Regeln:
- "label_de" ist ein kurzer deutscher Fachbegriff, wie er als Schlagwort in einer Bibliothek stehen würde (z. B. "Lineare Algebra", "Thermodynamik", "Qualitative Sozialforschung"), keine Sätze, keine Modulnummern.
- Keine Überschneidungen untereinander, keine sehr speziellen Forschungsthemen.
- "beschreibung_de": 1-2 Sätze, was das Thema im Grundstudium umfasst. "keywords_de": 5-8 Fachbegriffe. "label_en": englischer Fachbegriff.

Antworte ausschließlich mit JSON: {"themen": [{"label_de": "...", "label_en": "...", "beschreibung_de": "...", "keywords_de": [...]}]}"""


def gnd_lookup(label: str, pause: float = 0.2) -> dict | None:
    """Sucht ein GND-Sachschlagwort mit genau diesem Namen oder Synonym (lobid-gnd)."""
    norm = _norm(label)
    for q in (f'preferredName:"{label}"', f'variantName:"{label}"', label):
        url = "https://lobid.org/gnd/search?" + urllib.parse.urlencode(
            {"q": q, "filter": "type:SubjectHeading", "format": "json", "size": 10})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = json.load(r)
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError("lobid-gnd nicht erreichbar")
        time.sleep(pause)
        for m in data.get("member", []):
            names = [m.get("preferredName", "")] + m.get("variantName", [])
            if any(_norm(n) == norm for n in names):
                return {
                    "uri": m["id"], "gnd_id": m.get("gndIdentifier"), "label": m.get("preferredName"),
                    "sachgruppe": [{"uri": s["id"], "label": s.get("label")} for s in m.get("gndSubjectCategory", [])],
                    "oberbegriff": [{"uri": b["id"], "label": b.get("label")} for b in m.get("broaderTermGeneral", [])],
                }
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>", "", s)).strip().casefold()


def build_grundlagen(cfg: LLMConfig, pro_fach: int = 12, faecher: list[str] | None = None, on_progress=None,
                     stop_event=None):
    voc = load_vocab("hochschulfaechersystematik")
    alle = [c for c in voc.concepts if c["level"] == 2]
    if faecher:
        alle = [c for c in alle if c["notation"] in faecher]
    raw_path = CACHE / "grundlagen_vorschlaege.jsonl"
    meta = {"modell": cfg.model, "datum": date.today().isoformat(), "prompt_hash": _hash(GRUNDLAGEN_SYSTEM)}

    def worker(client: LLMClient, task: dict) -> dict:
        f = voc.by_uri[task["fach"]]
        sb = voc.by_uri[f["broader"]]
        user = (f"Studienfach: {clean_label(f['label'])} (Studienbereich {clean_label(sb['label'])}). "
                f"Nenne {pro_fach} Grundlagenthemen.")
        res = client.chat_json(GRUNDLAGEN_SYSTEM, user)
        themen = [t for t in res.get("themen", []) if t.get("label_de")]
        if not themen:
            raise ValueError("keine Themen")
        return {"id": task["id"], "fach": task["fach"], "themen": themen[:pro_fach]}

    tasks = [{"id": "fach:" + c["notation"], "fach": c["uri"]} for c in alle]
    prog = run_tasks(tasks, worker, raw_path, cfg, on_progress=on_progress, stop_event=stop_event)
    resolve_grundlagen(meta)
    return prog


def resolve_grundlagen(meta: dict | None = None):
    """Belegt die LLM-Vorschläge über die GND. Nur Treffer werden zu Themen."""
    voc = load_vocab("hochschulfaechersystematik")
    raw = load_jsonl(CACHE / "grundlagen_vorschlaege.jsonl")
    cache_path = CACHE / "gnd_lookup.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    meta = meta or {"datum": date.today().isoformat()}
    by_gnd: dict[str, dict] = {}
    misses = []
    for entry in raw:
        for t in entry["themen"]:
            label = t["label_de"].strip()
            if label not in cache:
                cache[label] = gnd_lookup(label)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            hit = cache[label]
            if not hit:
                misses.append((label, entry["fach"], voc.by_uri[entry["fach"]]["label"]))
                continue
            row = by_gnd.get(hit["uri"])
            if row is None:
                by_gnd[hit["uri"]] = row = {
                    "id": hit["uri"], "art": "grundlagen", "label_de": hit["label"], "label_en": t.get("label_en", ""),
                    "beschreibung_de": t.get("beschreibung_de", ""), "keywords_de": t.get("keywords_de", [])[:8],
                    "faecher_uris": [], "lehrebene": "grundlagen",
                    "gnd": {k: hit[k] for k in ("gnd_id", "sachgruppe", "oberbegriff")},
                    "quelle": {"name": "Gemeinsame Normdatei (GND)", "uri": hit["uri"], "lizenz": "CC0-1.0"},
                    "mapping": {"methode": "Grundlagenthema per LLM für das Studienfach vorgeschlagen, "
                                           "über GND-Sachschlagwort belegt (Label aus der GND)",
                                **meta},
                }
            if entry["fach"] not in row["faecher_uris"] and len(row["faecher_uris"]) < 3:
                row["faecher_uris"].append(entry["fach"])
    rows = []
    for r in by_gnd.values():
        r.update(_derive(voc, r.pop("faecher_uris")))
        rows.append(r)
    rows.sort(key=lambda r: (r["studienfach"][0]["notation"], r["label_de"]))
    (DIR / "themen_grundlagen.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                                 encoding="utf-8")
    with (DIR / "grundlagen_ohne_gnd.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["vorschlag", "studienfach_uri", "studienfach"])
        w.writerows(misses)
    return len(rows), len(misses)
