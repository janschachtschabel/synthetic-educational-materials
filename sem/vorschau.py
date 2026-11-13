"""Lesbare Übersicht über Seeds und Materialien (für die Vorschau in der Oberfläche)."""

from __future__ import annotations

import json
from pathlib import Path

from . import schema as S


def lade(path: Path | str, limit: int | None = None) -> list[dict]:
    out = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # halbe Zeile nach Absturz oder während ein Lauf schreibt
                if limit and len(out) >= limit:
                    break
    return out


def dateiart(path: Path) -> str:
    """„materialien“, wenn der erste Datensatz einen Volltext hat, sonst „seeds“."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    return "materialien" if ist_material(json.loads(line)) else "seeds"
    except (OSError, json.JSONDecodeError):
        pass
    return "seeds"


def ist_material(rec: dict) -> bool:
    return S.F_FULLTEXT in rec


def _labels(rec: dict, feld: str) -> str:
    return ", ".join(rec.get(feld + "_DISPLAYNAME") or rec.get(feld) or [])


def metadaten(rec: dict) -> dict[str, str]:
    """Feste Metadaten des Seeds als Label → Wert (leere Felder weggelassen)."""
    t = rec.get(S.F_THEMA, {})
    p = rec.get(S.F_PROVENANCE, {}).get(S.F_THEMA, {})
    beruf, komp = rec.get(S.F_BERUF) or {}, rec.get(S.F_KOMPETENZ) or {}
    umfang, stil = rec.get(S.F_UMFANG) or {}, rec.get(S.F_STIL) or {}
    zeilen = {
        "Bildungsstufe": _labels(rec, S.F_CONTEXT),
        "Studienfach": _labels(rec, S.F_TAXON_UNI),
        "Fach (OEH)": _labels(rec, S.F_TAXON),
        "Themenbaum (WLO)": _labels(rec, S.F_CURRICULUM),
        "Bildungsbereich (Kita)": _labels(rec, "synth:bildungsbereich_elementar"),
        "Programmbereich (VHS)": _labels(rec, "synth:programmbereich_vhs"),
        "Fachgebiet (VHS)": _labels(rec, "synth:fachgebiet_vhs"),
        "Berufsgruppe (KldB)": _labels(rec, S.F_PROFESSION),
        "Beruf (ESCO)": beruf.get("label", ""),
        "Beruf-URI": beruf.get("uri", ""),
        "Qualifizierungsweg": beruf.get("qualifizierung", ""),
        "Ausbildungsberuf (KMK)": "; ".join(beruf.get("ausbildungsberuf_kmk") or []),
        "Inhaltstyp": _labels(rec, S.F_LRT),
        "Zielgruppe": _labels(rec, S.F_ROLE),
        "Sprache": ", ".join(rec.get(S.F_LANGUAGE, [])),
        "Thema": "" if komp else t.get("label_de") or t.get("label_en", ""),
        "Themen-URI": "" if komp else t.get("id", ""),
        "Themenquelle": p.get("quelle", ""),
        "Kompetenz (ESCO)": " – ".join(x for x in (komp.get("label"), komp.get("relation"), komp.get("art")) if x),
        "Kompetenz-URI": komp.get("uri", ""),
        "RVK-Klasse": " – ".join(x for x in ((t.get("rvk") or {}).get("notation"), t.get("label_rvk"),
                                             " > ".join((t.get("rvk") or {}).get("pfad") or [])) if x),
        "DigComp-Kompetenz": " ".join(x for x in ((rec.get("synth:digcomp_kompetenz") or {}).get("id"),
                                                  (rec.get("synth:digcomp_kompetenz") or {}).get("label")) if x),
        "DigComp-Bereich": (rec.get("synth:digcomp_bereich") or {}).get("label", ""),
        "DigComp-Niveau": " – ".join(x for x in (rec.get("synth:digcomp_niveau"), rec.get("synth:digcomp_art"),
                                                 f"KI {rec['synth:digcomp_ki']}" if rec.get("synth:digcomp_ki") else "")
                                     if x),
        "GND-Motiv ersetzt": t.get("gnd_motiv") or "",
        "Themenbeschreibung aus": (t.get("beschreibung_quelle") or {}).get("name", ""),
        "Lehrebene": t.get("lehrebene") or "",
        "Niveau": (rec.get(S.F_NIVEAU) or {}).get("label", ""),
        "Niveau-URI": (rec.get(S.F_NIVEAU) or {}).get("exactMatch", ""),
        "Anforderungsbereich": rec.get("synth:anforderungsbereich", ""),
        "Förderschwerpunkt": rec.get("synth:foerderschwerpunkt", ""),
        "Bildungsstandards": (rec.get(S.F_BILDUNGSSTANDARDS) or {}).get("dokument", ""),
        "Lernfeld": " ".join(str(x) for x in ((rec.get(S.F_LERNFELD) or {}).get("nr"),
                                               (rec.get(S.F_LERNFELD) or {}).get("titel")) if x),
        "Veranstaltungsform": rec.get("synth:veranstaltungsform", ""),
        "Sprachniveau": rec.get("synth:sprachniveau", ""),
        "Textsorte": rec.get("synth:textsorte", ""),
        "Umfang": "{von}–{bis} Wörter".format(**umfang) if umfang else "",
        "Einstieg": stil.get("einstieg", ""),
        "Namen": ", ".join(stil.get("namen", [])),
        "Didaktische Funktion": rec.get(S.F_DIDAKTIK, ""),
        "Bearbeitungsdauer": f"{rec[S.F_LEARNING_TIME]} Minuten" if rec.get(S.F_LEARNING_TIME) else "",
        "Lernort": rec.get(S.F_LERNORT, ""),
        "Sozialform": rec.get("synth:sozialform", ""),
        "Querschnittsthema": rec.get(S.F_QUERSCHNITT, ""),
        "Status": ", ".join(f"{k}: {v}" for k, v in p.get("status", {}).items()),
        "Belege": ", ".join(p.get("belege", {}).get("erfuellt", [])),
    }
    return {k: str(v) for k, v in zeilen.items() if v}


def suchtext(rec: dict) -> str:
    t = rec.get(S.F_THEMA, {})
    teile = [rec.get("id", ""), t.get("label_de", ""), t.get("label_en", ""), _labels(rec, S.F_TAXON_UNI),
             _labels(rec, S.F_PROFESSION), (rec.get(S.F_BERUF) or {}).get("label", ""),
             _labels(rec, S.F_LRT), rec.get(S.F_TITLE, ""), " ".join(rec.get(S.F_KEYWORD, []))]
    return " ".join(teile).casefold()
