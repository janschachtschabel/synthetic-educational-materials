"""OEH-Fach (ccm:taxonid, Vokabular discipline) für Berufsbildung und Erwachsenenbildung.

Schule und Elementar setzen ccm:taxonid schon beim Seed, die Hochschule nutzt ccm:oeh_taxonid_university. Berufs-
und Erwachsenenbildung hatten bis 2026-11-13 kein Fach, obwohl WLO es dort fast immer setzt (WLO-Export: 97 % der
beruflichen, 100 % der EB-Materialien). Grundlage sind zwei Handmappings des Projekts (closeMatch):

- data/mappings/berufsbildung/kldb_discipline.json: KldB-Berufshauptgruppe → Fach, dazu immer „Berufliche Bildung“
- data/mappings/erwachsenenbildung/fachgebiete_discipline.json: VHS-Fachgebiet → Fach, sonst „Weiterbildung“

`python -m sem faecher-ergaenzen --materialien output/materialien_….jsonl` trägt das Fach in fertige Materialien
nach (ohne LLM, ohne Neugenerierung); vorhandene Fächer bleiben unverändert.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import schema as S
from .paths import MAPPING_DIR

KLDB_DATEI = MAPPING_DIR / "berufsbildung" / "kldb_discipline.json"
EB_DATEI = MAPPING_DIR / "erwachsenenbildung" / "fachgebiete_discipline.json"
F_FACHGEBIET = "synth:fachgebiet_vhs"
METHODE = "closeMatch (Handmapping des Projekts)"


@lru_cache(maxsize=1)
def _kldb() -> dict:
    return json.loads(KLDB_DATEI.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _eb() -> dict:
    return json.loads(EB_DATEI.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _disciplines():
    from .vocabs import load

    return load("discipline")


def fach_uris_berufsbildung(kldb_uris: list[str]) -> list[str]:
    """„Berufliche Bildung“ plus die Fächer der KldB-Berufshauptgruppen (erste zwei Ziffern der Kennziffer)."""
    m = _kldb()
    out = list(m["immer"])
    for u in kldb_uris:
        out += m["closeMatch"].get(u.rstrip("/").rsplit("/", 1)[-1][:2], [])
    return list(dict.fromkeys(out))


def fach_uris_erwachsenenbildung(fachgebiet_notationen: list[str]) -> list[str]:
    """Fächer der VHS-Fachgebiete; ohne Treffer der Ersatzwert („Weiterbildung“)."""
    m = _eb()
    out = [u for n in fachgebiet_notationen for u in m["closeMatch"].get(n, [])]
    return list(dict.fromkeys(out)) or list(m["ersatz"])


def _notation(uri: str) -> str:
    """VHS-Fachgebiet-Notation aus der Repo-URI (…programmbereiche.json#fg6.02 → 6.02)."""
    return uri.rsplit("#fg", 1)[-1] if "#fg" in uri else uri


def fach_refs(rec: dict) -> list[dict]:
    """Fach-Referenzen (URI, Label) für einen Seed oder ein Material der Berufs- bzw. Erwachsenenbildung."""
    b = rec.get(S.F_BEREICH)
    if b == "berufsbildung":
        uris = fach_uris_berufsbildung(rec.get(S.F_PROFESSION) or [])
    elif b == "erwachsenenbildung":
        uris = fach_uris_erwachsenenbildung([_notation(u) for u in rec.get(F_FACHGEBIET) or []])
    else:
        return []
    voc = _disciplines()
    return [voc.ref(u) for u in uris if u in voc.by_uri]


def setzen(rec: dict, methode: str = METHODE) -> bool:
    """Setzt ccm:taxonid für Berufs- und Erwachsenenbildung, falls noch leer. True, wenn gesetzt."""
    if rec.get(S.F_TAXON):
        return False
    refs = fach_refs(rec)
    if not refs:
        return False
    datei = KLDB_DATEI if rec.get(S.F_BEREICH) == "berufsbildung" else EB_DATEI
    S.set_controlled(rec, S.F_TAXON, refs, {
        **S.provenance("OpenEduHub discipline", uri=_disciplines().scheme["uri"], license="CC0-1.0", method=methode),
        "mapping": str(datei.relative_to(MAPPING_DIR.parent.parent)).replace("\\", "/")})
    return True


def standard_ausgabe(pfad: str | Path) -> Path:
    p = Path(pfad)
    return p.with_name(p.stem + "_faecher" + p.suffix)


def ergaenzen(eingabe: str | Path, ausgabe: str | Path) -> dict:
    """Schreibt die Materialien mit ergänztem Fach nach `ausgabe` (Reihenfolge und übrige Felder unverändert)."""
    zaehler = {"gesamt": 0, "ergaenzt": 0}
    je_bereich: dict[str, int] = {}
    with open(eingabe, encoding="utf-8") as f, open(ausgabe, "w", encoding="utf-8") as out:
        for zeile in f:
            if not zeile.strip():
                continue
            rec = json.loads(zeile)
            zaehler["gesamt"] += 1
            if setzen(rec, METHODE + ", nachträglich ergänzt (faecher-ergaenzen)"):
                zaehler["ergaenzt"] += 1
                je_bereich[rec[S.F_BEREICH]] = je_bereich.get(rec[S.F_BEREICH], 0) + 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {**zaehler, "je_bereich": je_bereich}
