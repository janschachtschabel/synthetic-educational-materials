"""Hochschulthemen aus der Regensburger Verbundklassifikation (RVK), vierte Themenquelle neben OpenAlex, GND und
den Fachprofilen.

Schritte:
  rvk_klassen        thematische Klassen der Tiefe 2 bis 5 aus dem lokalen MarcXML-Gesamtabzug (--rvk, Standard:
                     neueste Datei data/lokal/rvk/rvko_marcxml_*.xml.gz) → rvk_klassen.jsonl, rvk_auswahl.json
  rvk_kandidaten     ähnlichste Studienfächer je Klasse der Tiefe 2 und 3 (lokale Embeddings), plus Hinweise aus
                     dem Handmapping rvk_studienbereich.csv
  rvk_faecher        drei Stimmen je Klasse der Tiefe 2 und 3
  rvk_labels         eigenständiger Themenname für Benennungen, die nur im Pfad verständlich sind (LLM)
  rvk_lehrebene      drei Stimmen je Thema: Grundlagen, Vertiefung, Forschung oder kein Lehrthema
  rvk_zusammenfuehren  Fach erben (nächste abgestimmte Oberklasse), Abgleich mit den vorhandenen Themen (gleiche
                     GND-ID oder gleicher Name im selben Studienfach → Beleg statt neues Thema), Prüfliste, Ausschlüsse

Tiefere Klassen (Tiefe 4 und 5) werden nicht eigens Fächern zugeordnet: Sie erben das Fach der nächsten
Oberklasse der Tiefe 3 oder 2, über die abgestimmt wurde. Das spart rund 85 % der Fach-Abstimmungen; RVK-Klassen
liegen fast immer im Fach ihrer Oberklasse.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from ...llm import LLMClient, LLMConfig, run_tasks
from ...mapping import abstimmung, pruefung
from ...mapping.abstimmung import Ziel
from ...mapping.embeddings import DEFAULT_EMBEDDING_MODEL, Embedder
from ...paths import lokal_pfad
from ...quellen import rvk as R
from ...vocabs import load as load_vocab
from ..base import fertig
from . import DIR, load_jsonl
from . import build as B

CACHE = B.CACHE
KLASSEN = DIR / "rvk_klassen.jsonl"          # Auswahl (im Repo, CC0)
AUSWAHL_INFO = DIR / "rvk_auswahl.json"      # Quartalsstand, Zahlen je Typ und Tiefe, Ausschlüsse
BRUECKE = DIR / "rvk_studienbereich.csv"     # Handmapping Fachsystematik/Klasse → Studienbereich
PRUEF_RVK = DIR / "pruefliste_rvk.csv"
AUSGESCHLOSSEN_RVK = DIR / "rvk_ausgeschlossen.csv"
KANDIDATEN_RVK = CACHE / "hochschule_rvk_kandidaten.json"
STIMMEN_RVK_FAECHER = CACHE / "hochschule_stimmen_rvk_faecher.jsonl"
STIMMEN_RVK_LEHREBENE = CACHE / "hochschule_stimmen_rvk_lehrebene.jsonl"
LABELS_RVK = CACHE / "hochschule_rvk_labels.jsonl"
THEMEN_RVK = CACHE / "hochschule_themen_rvk.jsonl"  # Teilergebnis; Gesamtset: B.THEMEN_HOCHSCHULE
BELEGE_RVK = CACHE / "hochschule_rvk_belege.json"   # vorhandenes Thema → RVK-Klassen, die es belegen
MAX_FACH_TIEFE = 3
MEHRDEUTIG_AB = 4  # so viele Themen mit derselben Benennung: Name wird aus dem Pfad umformuliert


def standard_datei() -> Path | None:
    """Neuester Gesamtabzug unter data/lokal/rvk/ (Updates *.update.xml.gz zählen nicht)."""
    ps = [p for p in lokal_pfad("rvk").glob("rvko_marcxml_*.xml.gz") if ".update." not in p.name]
    return sorted(ps, key=lambda p: R.quartal(p))[-1] if ps else None


def info() -> dict:
    return json.loads(AUSWAHL_INFO.read_text(encoding="utf-8")) if AUSWAHL_INFO.exists() else {}


def quelle(row: dict | None = None) -> dict:
    q = {"name": R.QUELLE_NAME, "uri": R.DOWNLOAD, "lizenz": R.LIZENZ, "stand": info().get("quartal", "")}
    if row:
        q["uri"] = row["uri"]
        q["url"] = row["url"]
    return q


# --- 1. Klassen -------------------------------------------------------------------------------

def build_rvk_klassen(cfg=None, rvk_datei: str | None = None, on_progress=None, **_):
    pfad = Path(rvk_datei) if rvk_datei else standard_datei()
    if not pfad or not pfad.exists():
        return fertig(f"kein RVK-Abzug gefunden ({pfad or lokal_pfad('rvk')}); Download siehe data/README.md, "
                      "übersprungen")
    klassen = R.lade(pfad)
    sel, stat = R.auswaehlen(klassen)
    # Gespeichert wird je Klasse nur die Oberklasse; Pfade setzt lade_klassen() wieder zusammen. Die Oberklassen
    # aller Themen stehen mit thema=false in der Datei: Über die der Tiefe 2 und 3 wird abgestimmt, auch wenn sie
    # selbst kein Thema sind (z. B. „Allgemeines“), denn sie geben das Fach an ihre Unterklassen weiter.
    rows: dict[str, dict] = {}
    for s in sel:
        kette = list(zip(s["pfad_notationen"], s["pfad"]))
        for i, (nt, label) in enumerate(kette):
            rows.setdefault(nt, {"notation": nt, "label": label, "tiefe": i + 1,
                                 "ober": kette[i - 1][0] if i else None, "thema": False})
        rows[s["notation"]] = {"notation": s["notation"], "label": s["label_rvk"], "tiefe": s["tiefe"],
                               "ober": s["pfad_notationen"][-1], "thema": True,
                               **({"gnd": [[g["id"], g["label"]] for g in s["gnd"]]} if s["gnd"] else {}),
                               **({"register": s["register"]} if s["register"] else {})}
    ober = [r for r in rows.values() if not r["thema"] and 2 <= r["tiefe"] <= MAX_FACH_TIEFE]
    B._write_jsonl(KLASSEN, sorted(rows.values(), key=lambda r: r["notation"]))
    lade_klassen.cache_clear()
    AUSWAHL_INFO.write_text(json.dumps({
        "quelle": pfad.name, "quartal": R.quartal(pfad), "lizenz": R.LIZENZ, "download": R.DOWNLOAD,
        "datum": date.today().isoformat(), "max_tiefe": R.MAX_TIEFE, "statistik": stat,
        "ausgeschlossen": R.AUSGESCHLOSSEN, "oberklassen_nur_fach": len(ober)}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return fertig(f"{len(sel)} thematische Klassen (Tiefe 2–{R.MAX_TIEFE}: {stat['thematisch_je_tiefe']}), "
                  f"{len(ober)} Oberklassen nur für das Fach, Stand {R.quartal(pfad)}")


@lru_cache(maxsize=1)
def lade_klassen() -> list[dict]:
    """Klassen der Tiefe 2 bis 5 aus rvk_klassen.jsonl mit wieder zusammengesetztem Pfad (Benennungen und Notationen
    der Oberklassen ab der Fachsystematik), Fachsystematik, URI und Link."""
    if not KLASSEN.exists():
        raise FileNotFoundError("rvk_klassen.jsonl fehlt: erst den Schritt „rvk_klassen“ ausführen (--rvk).")
    roh = {r["notation"]: r for r in load_jsonl(KLASSEN)}
    out = []
    for r in roh.values():
        if r["tiefe"] < 2:
            continue
        kette, o = [], r["ober"]
        while o:
            kette.append(roh[o])
            o = roh[o]["ober"]
        kette.reverse()
        out.append({"notation": r["notation"], "label_rvk": r["label"], "tiefe": r["tiefe"], "thema": r["thema"],
                    "fachsystematik": {"notation": kette[0]["notation"], "label": kette[0]["label"]},
                    "pfad": [k["label"] for k in kette], "pfad_notationen": [k["notation"] for k in kette],
                    "gnd": [{"id": g[0], "label": g[1]} for g in r.get("gnd", [])], "register": r.get("register", []),
                    "uri": R.uri(r["notation"]), "url": R.url(r["notation"]),
                    "label_unklar": R.label_unklar(r["label"])})
    # Benennungen, die bei vielen Themen gleich lauten („Literaturgeschichte“ unter jeder Literatur), sind ohne Pfad
    # mehrdeutig und werden ebenfalls umformuliert
    haeufig = Counter(k["label_rvk"] for k in out if k["thema"])
    for k in out:
        if haeufig[k["label_rvk"]] >= MEHRDEUTIG_AB:
            k["label_unklar"] = True
    return out


def lade_bruecke() -> dict[str, dict]:
    """RVK-Notation (Fachsystematik oder Klasse) → {"studienbereiche": [URIs], "sicher": bool}."""
    voc = load_vocab("hochschulfaechersystematik")
    by_not = {c["notation"]: c["uri"] for c in voc.concepts if c["level"] == 1}
    out = {}
    with BRUECKE.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            uris = [by_not[n.strip()] for n in r["studienbereiche"].split("|") if n.strip() in by_not]
            out[r["rvk_notation"].strip()] = {"studienbereiche": uris, "sicher": r["sicher"].strip() == "ja"}
    return out


def bruecke_fuer(k: dict, bruecke: dict) -> dict | None:
    """Spezifischster Eintrag des Handmappings auf dem Pfad der Klasse (Klasse vor Fachsystematik)."""
    for nt in [k["notation"], *reversed(k["pfad_notationen"])]:
        if nt in bruecke:
            return bruecke[nt]
    return None


def _text(k: dict) -> str:
    return ". ".join(x for x in [k.get("label_de") or k["label_rvk"], "Teilgebiet von: " + " > ".join(k["pfad"]),
                                 ", ".join(g["label"] for g in k.get("gnd", [])[:8])] if x)


def _objekte(nur_thema: bool = False, max_tiefe: int | None = None, limit: int | None = None) -> list[dict]:
    labels = lade_labels()
    out = []
    for k in lade_klassen():
        if (nur_thema and not k["thema"]) or (max_tiefe and k["tiefe"] > max_tiefe):
            continue
        k = {**k, "label_de": labels.get(k["notation"], k["label_rvk"])}
        out.append({"id": k["uri"], "klasse": k, "text": _text(k),
                    "prompt": {"thema": k["label_de"], "rvk_benennung": k["label_rvk"],
                               "oberklassen": " > ".join(k["pfad"]),
                               "schlagwoerter": [g["label"] for g in k.get("gnd", [])[:6]]}})
    return out[:limit] if limit else out


# --- 2. Kandidaten und Fächer ---------------------------------------------------------------------

def build_rvk_kandidaten(cfg=None, limit: int | None = None, embedding_modell: str | None = None,
                         embedder: Embedder | None = None, **_):
    objekte = _objekte(max_tiefe=MAX_FACH_TIEFE, limit=limit)
    bruecke = lade_bruecke()
    hinweise = {o["id"]: set((bruecke_fuer(o["klasse"], bruecke) or {}).get("studienbereiche", [])) for o in objekte}
    emb = embedder or Embedder(embedding_modell or DEFAULT_EMBEDDING_MODEL)
    out = B.kandidaten_berechnen(objekte, emb, hinweise)
    KANDIDATEN_RVK.parent.mkdir(parents=True, exist_ok=True)
    KANDIDATEN_RVK.write_text(json.dumps({"embedding_modell": emb.model, "themen": out}, ensure_ascii=False),
                              encoding="utf-8")
    return fertig(f"{len(out)} RVK-Klassen der Tiefe 2–{MAX_FACH_TIEFE} mit Kandidaten, Modell {emb.model}")


FAECHER_HINWEIS = ("\nDie Themen sind Klassen der Regensburger Verbundklassifikation (Bibliothekssystematik). "
                   "\"oberklassen\" nennt die übergeordneten Klassen von der Fachsystematik abwärts; das Thema liegt "
                   "fast immer im Fach dieser Oberklassen.")


def build_rvk_faecher(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                      stimmen: int = B.N_STIMMEN, **_):
    voc = load_vocab("hochschulfaechersystematik")
    kand = B.load_kandidaten(KANDIDATEN_RVK)["themen"]
    objekte = [o for o in _objekte(max_tiefe=MAX_FACH_TIEFE, limit=limit) if o["id"] in kand]
    return abstimmung.abstimmen(objekte, {o["id"]: list(kand[o["id"]]["kandidaten"]) for o in objekte},
                                B._fach_ziele(voc),
                                B._systeme(B.FAECHER_PERSPEKTIVEN, B.FAECHER_REGELN + FAECHER_HINWEIS, stimmen),
                                STIMMEN_RVK_FAECHER, cfg, max_auswahl=3, batch=10, on_progress=on_progress,
                                stop_event=stop_event)


# --- 3. Themennamen --------------------------------------------------------------------------------

LABELS_SYSTEM = """Du formulierst Themennamen für ein deutsches Bildungsdatenset. Gegeben sind Klassen der Regensburger Verbundklassifikation (RVK): die Benennung der Klasse und ihre Oberklassen. Viele Benennungen sind nur im Pfad verständlich, z. B. "Theorie" unter "Makroökonomie".

Für jede Klasse lieferst du "label_de": einen eigenständigen deutschen Themennamen (höchstens 8 Wörter), der ohne den Pfad verständlich ist und genau das Thema der Klasse trifft, z. B. "Makroökonomische Theorie". Nimm den nötigen Zusammenhang aus den Oberklassen, erfinde nichts dazu. Ist die Benennung schon allein verständlich, gib sie unverändert zurück.

Antworte ausschließlich mit JSON: {"ergebnisse": [{"id": "...", "label_de": "..."}]}"""


def lade_labels() -> dict[str, str]:
    """Notation → umformulierter Themenname (Zwischenspeicher; ohne ihn aus dem fertigen Teilergebnis)."""
    out = {}
    for r in B.teil_laden(THEMEN_RVK, "rvk"):
        if r.get("label_de") and r["label_de"] != r.get("label_rvk"):
            out[r["rvk"]["notation"]] = r["label_de"]
    if LABELS_RVK.exists():
        for b in load_jsonl(LABELS_RVK):
            out.update(b["labels"])
    return out


def build_rvk_labels(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None, batch: int = 25,
                     **_):
    vorhanden = lade_labels()
    todo = [k for k in lade_klassen() if k["thema"] and k["label_unklar"] and k["notation"] not in vorhanden]
    todo = todo[:limit] if limit else todo
    tasks = [{"id": "labels:" + c[0]["notation"] + "|" + c[-1]["notation"], "klassen": c}
             for c in (todo[i:i + batch] for i in range(0, len(todo), batch))]

    def worker(client: LLMClient, task: dict) -> dict:
        items = [{"id": k["notation"], "benennung": k["label_rvk"], "oberklassen": " > ".join(k["pfad"])}
                 for k in task["klassen"]]
        res = client.chat_json(LABELS_SYSTEM, json.dumps(items, ensure_ascii=False))
        by_id = {str(r.get("id")): r for r in res.get("ergebnisse", []) if isinstance(r, dict)}
        out = {}
        for k in task["klassen"]:
            lab = str((by_id.get(k["notation"]) or {}).get("label_de") or "").strip()
            if lab:
                out[k["notation"]] = lab[:120]
        if len(out) * 2 < len(task["klassen"]):
            raise ValueError("zu wenige Themennamen in der Antwort")
        return {"id": task["id"], "labels": out}

    return run_tasks(tasks, worker, LABELS_RVK, cfg, on_progress=on_progress, stop_event=stop_event)


# --- 4. Lehrebene -----------------------------------------------------------------------------------

LEHREBENE_ZIELE = {**B.LEHREBENE_ZIELE,
                   "kein_lehrthema": Ziel("kein_lehrthema", "kein_lehrthema", "kein Lehrthema",
                                          "Form-, Sammel- oder Verwaltungsklasse der Bibliothek (z. B. Zeitschriften, "
                                          "Sammelwerke, Kataloge), einzelner Ort, Person oder Gegenstand, oder ohne "
                                          "Lehrbezug")}
LEHREBENE_REGELN = B.LEHREBENE_REGELN + (
    "\nDie Themen sind Klassen der Regensburger Verbundklassifikation; \"oberklassen\" nennt den Zusammenhang. "
    "Ist eine Klasse kein Lehrinhalt, sondern eine Gruppe für Literaturformen (Zeitschriften, Festschriften, "
    "Sammelwerke, Kataloge), ein einzelner Ort, eine einzelne Institution oder Person, wähle \"kein_lehrthema\".")


def build_rvk_lehrebene(cfg: LLMConfig, limit: int | None = None, on_progress=None, stop_event=None,
                        stimmen: int = B.N_STIMMEN, **_):
    objekte = _objekte(nur_thema=True, limit=limit)
    alle = list(LEHREBENE_ZIELE)
    return abstimmung.abstimmen(objekte, {o["id"]: alle for o in objekte}, LEHREBENE_ZIELE,
                                B._systeme(B.LEHREBENE_PERSPEKTIVEN, LEHREBENE_REGELN, stimmen),
                                STIMMEN_RVK_LEHREBENE, cfg, max_auswahl=1, batch=25, on_progress=on_progress,
                                stop_event=stop_event)


# --- 5. Zusammenführen --------------------------------------------------------------------------------

METHODE = ("RVK-Klasse (thematisch, Tiefe 2–5); Fach per Embedding-Kandidaten, Handmapping Fachsystematik → "
           "Studienbereich und LLM-Mehrfachabstimmung auf Tiefe 2 und 3, tiefere Klassen erben das Fach der nächsten "
           "abgestimmten Oberklasse; Lehrebene per LLM-Mehrfachabstimmung")
_NORM = re.compile(r"[^0-9a-zäöüß]+")


def norm(label: str) -> str:
    return _NORM.sub(" ", re.sub(r"\s*[(<].*?[)>]", "", label.casefold())).strip()


def _fach_entscheidungen(stimmen: int, voc, by_not) -> dict[str, dict]:
    """Klassen-URI der Tiefe 2/3 → {"codes", "status", "konfidenz", "details", "verworfen", "ersatz"?}."""
    kand = B.load_kandidaten(KANDIDATEN_RVK)["themen"] if KANDIDATEN_RVK.exists() else {}
    st = abstimmung.lade_stimmen(STIMMEN_RVK_FAECHER)
    out = {}
    for uri, sf in st.items():
        if uri not in kand:
            continue
        sims = kand[uri]["kandidaten"]
        fa = abstimmung.auswerten(sf, stimmen, "", aehnlichkeit=sims)
        codes = [c for c in fa["auswahl"] if c in by_not]
        e = {"status": fa["status"], "konfidenz": fa["details"][0]["konfidenz"] if fa["details"] else 0.0,
             "faecher": [{**voc.ref(by_not[d["code"]]), **{k: v for k, v in d.items() if k != "code"}}
                         for d in fa["details"] if d["code"] in by_not],
             "verworfen": {voc.by_uri[by_not[c]]["label"]: n for c, n in fa["verworfen"].items() if c in by_not},
             "handmapping": kand[uri].get("handmapping", [])}
        if not codes:
            codes = [max(sims, key=sims.get)]
            e.update(status="unsicher", konfidenz=0.0, ersatz="kein Fach gewählt; ähnlichstes Fach laut Embedding")
        e["codes"] = codes
        e["handmapping_bestaetigt"] = any(voc.by_uri[by_not[c]]["broader"] in e["handmapping"] for c in codes)
        out[uri] = e
    return out


def _bruecke_fach(k: dict, bruecke: dict, voc) -> list[str]:
    """Ersatz ohne Abstimmung: erstes Studienfach im Studienbereich des Handmappings (für Klassen, deren
    Oberklassen nicht abgestimmt sind)."""
    b = bruecke_fuer(k, bruecke)
    for sb in (b or {}).get("studienbereiche", []):
        faecher = sorted((c for c in voc.concepts if c.get("broader") == sb), key=lambda c: c["notation"])
        if faecher:
            return [faecher[0]["notation"]]
    return []


def _lehrebene(st: dict | None, stimmen: int, tiefe: int) -> dict:
    if not st:
        # ohne Abstimmung: Heuristik nach Tiefe, ausdrücklich als unsicher gekennzeichnet
        return {"ebene": "grundlagen" if tiefe <= 3 else "vertiefung", "status": "unsicher",
                "methode": "Heuristik nach Tiefe (keine Abstimmung)", "stimmen": {}, "begruendungen": []}
    lv = abstimmung.auswerten(st, stimmen, max_auswahl=1)
    return {"ebene": lv["auswahl"][0] if lv["auswahl"] else "vertiefung", "status": lv["status"],
            "konfidenz": lv["details"][0]["konfidenz"] if lv["details"] else 0.0,
            "stimmen": {**{d["code"]: d["stimmen"] for d in lv["details"]}, **lv["verworfen"]},
            "begruendungen": lv["details"][0]["begruendungen"] if lv["details"] else []}


def _vorhandene() -> list[dict]:
    """OpenAlex- und GND-Themen (Teilergebnisse) für den Abgleich."""
    from . import gnd_themen as G

    return B.teil_laden(B.THEMEN_PATH, "openalex") + B.teil_laden(G.THEMEN_GND, "gnd")


def build_rvk_zusammenfuehren(cfg: LLMConfig | None = None, stimmen: int = B.N_STIMMEN, modell: str | None = None,
                              **_):
    if not KLASSEN.exists():
        return fertig("keine RVK-Klassen (Schritt rvk_klassen), übersprungen")
    voc = load_vocab("hochschulfaechersystematik")
    by_not = B.faecher_by_not(voc)
    klassen = lade_klassen()
    labels = lade_labels()
    entscheid = _fach_entscheidungen(stimmen, voc, by_not)
    le_st = abstimmung.lade_stimmen(STIMMEN_RVK_LEHREBENE)
    korr = pruefung.lade_korrekturen(B.KORREKTUREN_PATH)
    bruecke = lade_bruecke()
    kand_all = B.load_kandidaten(KANDIDATEN_RVK) if KANDIDATEN_RVK.exists() else {}
    meta = B._meta(cfg, modell, kand_all, stimmen, METHODE, "".join(B.FAECHER_PERSPEKTIVEN) + B.FAECHER_REGELN
                   + FAECHER_HINWEIS + LABELS_SYSTEM + LEHREBENE_REGELN)
    # Abgleich: GND-ID und normierter Name je Studienfach der vorhandenen Themen
    idx_gnd: dict[str, list[dict]] = defaultdict(list)
    idx_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in _vorhandene():
        if t["id"].startswith("https://d-nb.info/gnd/"):
            idx_gnd[t["id"].rsplit("/", 1)[-1]].append(t)
        for f in t["studienfach"]:
            idx_name[(f["uri"], norm(t["label_de"]))].append(t)
    stand = info().get("quartal", "")
    rows, pruef, aus = [], [], []
    belege: dict[str, list[dict]] = defaultdict(list)
    n = Counter()
    for k in klassen:
        if not k["thema"]:
            continue
        uri = k["uri"]
        k_korr = korr.get(uri)
        if k_korr and k_korr["ausschliessen"]:
            n["korrektur_ausgeschlossen"] += 1
            continue
        # Fach: eigene Abstimmung, sonst nächste abgestimmte Oberklasse, sonst Handmapping
        herkunft, e = None, None
        for nt in [k["notation"], *reversed(k["pfad_notationen"])]:
            e = entscheid.get(R.uri(nt))
            if e:
                herkunft = nt
                break
        gruende = []
        if e:
            codes = list(e["codes"])
            mapping = {**meta, "status": e["status"], "konfidenz": e["konfidenz"],
                       "handmapping_bestaetigt": e["handmapping_bestaetigt"], "faecher": e["faecher"],
                       "verworfen": e["verworfen"]}
            if herkunft != k["notation"]:
                mapping["fach_geerbt_von"] = herkunft
            if e.get("ersatz"):
                mapping["ersatz"] = e["ersatz"]
                gruende.append("keine Stimme für ein Fach, Vorschlag nur aus Embedding")
            elif e["status"] == "unsicher":
                gruende.append("keine Mehrheit für ein Fach" + (f" (geerbt von {herkunft})" if herkunft != k["notation"]
                                                                 else ""))
        else:
            codes = _bruecke_fach(k, bruecke, voc)
            if not codes:
                n["ohne_fach"] += 1
                continue
            mapping = {**meta, "status": "unsicher", "konfidenz": 0.0, "handmapping_bestaetigt": True,
                       "faecher": [], "verworfen": {},
                       "ersatz": "keine abgestimmte Oberklasse; erstes Fach im Studienbereich des Handmappings"}
            gruende.append("Fach nur aus dem Handmapping")
        b = bruecke_fuer(k, bruecke)
        if b and not b["sicher"]:
            mapping["handmapping_unsicher"] = True
        le = _lehrebene(le_st.get(uri), stimmen, k["tiefe"])
        if le["ebene"] == "kein_lehrthema" and not k_korr:
            aus.append({"id": uri, "notation": k["notation"], "label": k["label_rvk"], "pfad": " > ".join(k["pfad"]),
                        "stimmen": json.dumps(le["stimmen"], ensure_ascii=False),
                        "begruendung": (le["begruendungen"] or [""])[0]})
            n["kein_lehrthema"] += 1
            continue
        if le["ebene"] == "kein_lehrthema":
            le["ebene"] = "vertiefung"
        mapping["lehrebene"] = {x: le[x] for x in ("status", "konfidenz", "stimmen", "begruendungen", "methode")
                                if x in le}
        if le["status"] == "unsicher" and le.get("stimmen"):
            gruende.append("keine Mehrheit für die Lehrebene")
        label_de = labels.get(k["notation"], k["label_rvk"])
        if label_de != k["label_rvk"]:
            mapping["label_umformuliert"] = "eigenständiger Themenname per LLM (Benennung nur im Pfad verständlich)"
        if k_korr:
            if k_korr["codes"]:
                codes = [c for c in k_korr["codes"] if c in by_not][:3] or codes
                mapping["status"], mapping["konfidenz"] = "redaktionell", 1.0
            if k_korr["lehrebene"] in B.LEHREBENEN:
                le["ebene"] = k_korr["lehrebene"]
                mapping["lehrebene"]["status"] = "redaktionell"
            mapping["korrektur"] = k_korr
            gruende = []
        fach_uris = [by_not[c] for c in codes if c in by_not]
        # Abgleich mit vorhandenen Themen: gleiche GND-ID oder gleicher Name im selben Studienfach
        treffer = []
        for g in k.get("gnd", []):
            treffer += [t for t in idx_gnd.get(g["id"], []) if {f["uri"] for f in t["studienfach"]} & set(fach_uris)]
        for f in fach_uris:
            for name in {norm(label_de), norm(k["label_rvk"])}:
                treffer += idx_name.get((f, name), [])
        if treffer and not k_korr:
            for t in {t["id"]: t for t in treffer}.values():
                belege[t["id"]].append({"notation": k["notation"], "label": k["label_rvk"],
                                        "pfad": " > ".join(k["pfad"]), "uri": uri})
            n["verknuepft"] += 1
            continue
        row = {
            "id": uri, "art": "rvk", "label_de": label_de, "label_rvk": k["label_rvk"], "label_en": "",
            "beschreibung_de": f"Teilgebiet von: {' > '.join(k['pfad'])}",
            "keywords_de": list(dict.fromkeys([g["label"] for g in k.get("gnd", [])] + k.get("register", [])))[:10],
            **B._derive(voc, fach_uris), "lehrebene": le["ebene"],
            "rvk": {"notation": k["notation"], "tiefe": k["tiefe"], "pfad": k["pfad"],
                    "pfad_notationen": k["pfad_notationen"], "fachsystematik": k["fachsystematik"], "url": k["url"],
                    "stand": stand},
            "gnd": [{"id": g["id"], "uri": "https://d-nb.info/gnd/" + g["id"], "label": g["label"]}
                    for g in k.get("gnd", [])],
            "quelle": quelle(k), "mapping": mapping,
        }
        rows.append(row)
        n["neu"] += 1
        if gruende:
            pruef.append(B.pruefzeile(row, gruende, stimmen, notation=k["notation"], rvk_pfad=" > ".join(k["pfad"]),
                                      fach_geerbt_von=mapping.get("fach_geerbt_von", "")))
    rows.sort(key=lambda r: (r["studienfach"][0]["notation"], r["rvk"]["notation"]))
    B._write_jsonl(THEMEN_RVK, rows)
    BELEGE_RVK.write_text(json.dumps(belege, ensure_ascii=False), encoding="utf-8")
    pruefung.schreibe_pruefliste(PRUEF_RVK, pruef)
    pruefung.schreibe_pruefliste(AUSGESCHLOSSEN_RVK, aus)
    return fertig(f"{n['neu']} neue RVK-Themen, {n['verknuepft']} Klassen als Beleg an "
                  f"{len(belege)} vorhandene Themen, {n['kein_lehrthema']} kein Lehrthema, "
                  f"{len(pruef)} auf der Prüfliste")


def lade_belege() -> dict[str, list[dict]]:
    return json.loads(BELEGE_RVK.read_text(encoding="utf-8")) if BELEGE_RVK.exists() else {}


def statistik(rows: list[dict]) -> dict:
    """Kennzahlen der RVK-Themen und Abdeckung der Studienfächer vor/nach (ohne bzw. mit RVK)."""
    rvk = [r for r in rows if r.get("art") == "rvk"]
    vorher = Counter(r["studienfach"][0]["uri"] for r in rows if r.get("art") != "rvk")
    nachher = Counter(r["studienfach"][0]["uri"] for r in rows)
    voc = load_vocab("hochschulfaechersystematik")
    faecher = [c for c in voc.concepts if c["level"] == 2]
    schwach = sorted(faecher, key=lambda c: vorher[c["uri"]])[:25]
    return {
        **B.statistik(rvk),
        "je_tiefe": dict(sorted(Counter(r["rvk"]["tiefe"] for r in rvk).items())),
        "label_umformuliert": sum(1 for r in rvk if r["mapping"].get("label_umformuliert")),
        "fach_geerbt": sum(1 for r in rvk if r["mapping"].get("fach_geerbt_von")),
        "vorhandene_themen_mit_rvk_beleg": sum(1 for r in rows if (r.get("belege") or {}).get("rvk")),
        "faecher_ohne_thema": {"vorher": sum(1 for c in faecher if not vorher[c["uri"]]),
                               "nachher": sum(1 for c in faecher if not nachher[c["uri"]])},
        "schwach_besetzte_faecher": [{"fach": c["label"], "vorher": vorher[c["uri"]], "nachher": nachher[c["uri"]]}
                                     for c in schwach],
        "stand": info().get("quartal"),
    }
