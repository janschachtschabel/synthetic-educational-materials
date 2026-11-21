"""Gezielte Korrekturen an einem fertigen Datensatz: nur betroffene Materialien, nur betroffene Felder.

    python -m sem korrektur vorbereiten --materialien output/materialien_….jsonl [--name korrektur_hochschule]
    python -m sem materialien --seeds output/seeds_korrektur_hochschule.jsonl --anbieter anthropic --batch --warten
    python -m sem korrektur zusammenfuehren --materialien output/materialien_….jsonl --out output/materialien_…_korrigiert.jsonl

Entstanden nach der Prüfung des Hochschul-Datensatzes (2026-11-18). Ausgewählt werden:

- RVK-Themen mit Klammerzusatz („Kernphysik (ohne Spaltung und Streuung)“): Der Zusatz wurde als Fachaussage in den
  Text übernommen. Diese Materialien werden mit den aktuellen Prompts ganz neu erzeugt (gleicher Seed).
- Frage-Antwort-Paare mit mindestens FIKTIV_MIN Fragen zu den erfundenen Figuren des Textes (Namen aus synth:stil,
  etwa „Was rät Frau Krüger?“): nur die Paare werden neu erzeugt.
- Beschreibungen, die einen Vokabular-Zusatz übernommen haben („auch als E-Book“): nur die Beschreibung neu.
- Suchanfragen: Von mehreren Anfragen mit Stufenbegriff („… hochschule“, „… studium“) bleibt die erste, die übrigen
  werden gestrichen, solange mindestens SUCHANFRAGEN_MIN bleiben. Das kostet nichts und passiert beim Zusammenführen.

„vorbereiten“ schreibt die ausgewählten Materialien als Seed-Datei und legt für die Feld-Korrekturen den Stand der
Batch-Verarbeitung so an, dass alle übrigen Felder schon fertig sind: Der normale Batch-Lauf stellt dann nur die
fehlenden Anfragen. „zusammenfuehren“ übernimmt aus dem Ergebnis nur die korrigierten Felder in eine neue Datei;
bisherige Werte stehen in den *_alt-Feldern, Angaben zur Korrektur unter synth:generierung.korrektur. Materialien,
deren Korrektur scheiterte, bleiben unverändert. Die Eingabedatei wird nie verändert.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from . import schema as S
from .batch import Zustaende, batch_ordner
from .bereiche.hochschule import thema_und_abgrenzung
from .paths import ausgabe_pfad

FIKTIV_MIN = 3
SUCHANFRAGEN_MIN = 4
F_BESCHREIBUNG_ALT = "synth:beschreibung_alt"   # wie bei beschreibung-einstieg; Export: description_previous
F_QA_ALT = "synth:frage_antwort_paare_alt"
F_SUCHANFRAGEN_ALT = "synth:suchanfragen_alt"
VOLL = "voll"
FELD_SCHEMA = {"beschreibung": S.F_DESCRIPTION, "fragen_antworten": S.F_QA}
FELD_ALT = {"beschreibung": F_BESCHREIBUNG_ALT, "fragen_antworten": F_QA_ALT}
GRUND = {VOLL: "RVK-Klammerzusatz als Fachaussage: Material mit aktuellen Prompts neu erzeugt",
         "beschreibung": "Beschreibung übernahm einen Vokabular-Zusatz (E-Book)",
         "fragen_antworten": f"mindestens {FIKTIV_MIN} Fragen zu erfundenen Figuren des Textes"}

STUFE = re.compile(r"\b(hochschule|studium|uni|universität|vorlesung|bachelor|master|studierende)\b", re.I)
_EBOOK = re.compile(r"e-book", re.I)

# grobe Kosten je Material (Anthropic Haiku 5.5, Batch), gemessen im Hochschul-Lauf
KOSTEN_USD = {VOLL: 0.0033, "fragen_antworten": 0.0009, "beschreibung": 0.0003}


def fiktive_fragen(rec: dict) -> int:
    """Fragen, die eine erfundene Figur des Textes nennen: einen der vorgegebenen Vornamen oder „Herr/Frau X“."""
    namen = [x.strip() for x in (rec.get(S.F_STIL) or {}).get("namen", []) if x.strip()]
    vornamen = [x for x in namen if " " not in x]
    nachnamen = [x.split()[-1] for x in namen if x.startswith(("Herr ", "Frau "))]
    muster = [re.compile(r"\b" + re.escape(v) + r"\b") for v in vornamen]
    muster += [re.compile(r"\b(Herrn?|Frau)\s+" + re.escape(n) + r"\b") for n in nachnamen]
    return sum(any(m.search(p.get("frage", "")) for m in muster) for p in rec.get(S.F_QA) or [])


def korrektur(rec: dict) -> str | list[str] | None:
    """VOLL (ganz neu erzeugen), die neu zu erzeugenden Felder oder None."""
    t = rec.get(S.F_THEMA) or {}
    if t.get("rvk") and thema_und_abgrenzung(t.get("label_de") or "")[1]:
        return VOLL
    felder = []
    if _EBOOK.search(rec.get(S.F_DESCRIPTION) or ""):
        felder.append("beschreibung")
    if fiktive_fragen(rec) >= FIKTIV_MIN:
        felder.append("fragen_antworten")
    return felder or None


def suchanfragen_kuerzen(anfragen: list[str]) -> list[str]:
    """Nur die erste Anfrage mit Stufenbegriff behalten, solange mindestens SUCHANFRAGEN_MIN übrig bleiben."""
    gesehen, out = False, []
    for q in anfragen:
        if STUFE.search(q):
            if gesehen:
                continue
            gesehen = True
        out.append(q)
    return out if len(out) >= SUCHANFRAGEN_MIN else list(anfragen)


def _zustand(rec: dict, neu: list[str]) -> dict:
    """Stand der Batch-Verarbeitung, in dem alle Felder außer den neu zu erzeugenden fertig sind."""
    felder = {"volltext": rec[S.F_FULLTEXT], "titel": rec[S.F_TITLE], "beschreibung": rec[S.F_DESCRIPTION],
              "keywords": rec[S.F_KEYWORD], "fragen_antworten": rec[S.F_QA]}
    if S.F_LERNZIELE in rec:
        felder["lernziele"] = [{k: v for k, v in z.items() if k != "anforderungsbereich"} for z in rec[S.F_LERNZIELE]]
    if S.F_SUCHANFRAGEN in rec:
        felder["suchanfragen"] = rec[S.F_SUCHANFRAGEN]
    # ältere Materialien ohne Entitäten: None, damit die Korrektur sie nicht nebenbei erzeugt
    felder["entitaeten"] = rec.get(S.F_ENTITAETEN)
    if chk := rec.get(S.F_LRT_CHECK):
        felder["inhaltstyp"] = {"uri": chk["erkannt"], "name": chk["erkannt_label"]}
    for f in neu:
        felder.pop(f)
    return {"felder": felder}


def pfade(name: str) -> dict:
    seeds = ausgabe_pfad("seeds", name)
    out = ausgabe_pfad("materialien", name)
    return {"seeds": seeds, "ergebnis": out, "auswahl": seeds.with_name(seeds.stem.removeprefix("seeds_") + "_auswahl.json"),
            "zustaende": batch_ordner(out) / "materialien.sqlite", "stand": batch_ordner(out) / "stand.json"}


def vorbereiten(materialien: Path, name: str = "korrektur_hochschule") -> dict:
    p = pfade(name)
    if p["stand"].exists() or p["ergebnis"].exists():
        raise ValueError(f"Der Korrekturlauf {name!r} wurde schon gestartet; für eine neue Auswahl einen anderen "
                         "Namen wählen.")
    auswahl, zaehler = {}, Counter()
    p["seeds"].parent.mkdir(parents=True, exist_ok=True)
    zust = Zustaende(p["zustaende"])
    try:
        with Path(materialien).open(encoding="utf-8") as f, p["seeds"].open("w", encoding="utf-8") as s:
            for z in f:
                if not z.strip():
                    continue
                rec = json.loads(z)
                zaehler["materialien"] += 1
                k = korrektur(rec)
                if rec.get(S.F_SUCHANFRAGEN) and suchanfragen_kuerzen(rec[S.F_SUCHANFRAGEN]) != rec[S.F_SUCHANFRAGEN]:
                    zaehler["suchanfragen_gekuerzt"] += 1
                if not k:
                    continue
                auswahl[rec["id"]] = k
                for x in [k] if k == VOLL else k:
                    zaehler[x] += 1
                if k != VOLL:
                    zust[rec["id"]] = _zustand(rec, k)
                s.write(json.dumps(rec, ensure_ascii=False) + "\n")
        zust.commit()
    finally:
        zust.close()
    p["auswahl"].write_text(json.dumps(auswahl, ensure_ascii=False), encoding="utf-8")
    zaehler["kosten_usd"] = round(sum(KOSTEN_USD[x] * zaehler[x] for x in KOSTEN_USD), 2)
    return dict(zaehler)


def _korrigiert(alt: dict, neu: dict, k, meta: dict) -> dict:
    if k == VOLL:
        out = dict(neu)
        out[S.F_GENERATION] = {**(neu.get(S.F_GENERATION) or {}),
                               "korrektur": {**meta, "neu_erzeugt": True, "grund": GRUND[VOLL]}}
        return out
    out = dict(alt)
    prov = dict(out.get(S.F_PROVENANCE) or {})
    for f in k:
        feld = FELD_SCHEMA[f]
        out.setdefault(FELD_ALT[f], alt[feld])  # bei wiederholter Korrektur bleibt der ursprüngliche Wert
        out[feld] = neu[feld]
        prov[feld] = dict(S.provenance("LLM", method=f"aus Volltext und festen Metadaten neu erzeugt ({f})"),
                          modell=meta.get("modell"), datum=meta["datum"])
    out[S.F_PROVENANCE] = prov
    gen = neu.get(S.F_GENERATION) or {}
    info = {**meta, "felder": list(k), "grund": [GRUND[f] for f in k]}
    if gen.get("tokens"):
        info["tokens"] = gen["tokens"]
    out[S.F_GENERATION] = {**(alt.get(S.F_GENERATION) or {}), "korrektur": info}
    return out


def zusammenfuehren(materialien: Path, out_path: Path, name: str = "korrektur_hochschule") -> dict:
    p = pfade(name)
    materialien, out_path = Path(materialien), Path(out_path)
    if out_path.resolve() in (materialien.resolve(), p["ergebnis"].resolve()):
        raise ValueError("Ausgabe muss eine neue Datei sein")
    auswahl = json.loads(p["auswahl"].read_text(encoding="utf-8"))
    neu = {}
    if p["ergebnis"].exists():
        with p["ergebnis"].open(encoding="utf-8") as f:
            for z in f:
                if z.strip():
                    r = json.loads(z)
                    neu[r["id"]] = r
    zaehler = Counter()
    datum = date.today().isoformat()
    with materialien.open(encoding="utf-8") as f, out_path.open("w", encoding="utf-8") as o:
        for z in f:
            if not z.strip():
                continue
            rec = json.loads(z)
            zaehler["materialien"] += 1
            k = auswahl.get(rec["id"])
            if k:
                if rec["id"] in neu:
                    n = neu[rec["id"]]
                    rec = _korrigiert(rec, n, k, {"datum": datum,
                                                  "modell": (n.get(S.F_GENERATION) or {}).get("modell")})
                    zaehler["korrigiert_" + (VOLL if k == VOLL else "+".join(k))] += 1
                else:
                    zaehler["unveraendert_korrektur_gescheitert"] += 1
            q = rec.get(S.F_SUCHANFRAGEN)
            if q and (kurz := suchanfragen_kuerzen(q)) != q:
                rec.setdefault(F_SUCHANFRAGEN_ALT, q)
                rec[S.F_SUCHANFRAGEN] = kurz
                zaehler["suchanfragen_gekuerzt"] += 1
                zaehler["suchanfragen_gestrichen"] += len(q) - len(kurz)
            o.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dict(zaehler)
