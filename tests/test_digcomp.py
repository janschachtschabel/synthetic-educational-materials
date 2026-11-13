"""DigComp 3.0: Errata, Handmapping, Seeds in Erwachsenen- und Berufsbildung, Export (ohne Netz und ohne Rohdatei)."""

import json
import sys
from pathlib import Path

import pytest

from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.erwachsenenbildung import digcomp as DE
from sem.bereiche.erwachsenenbildung import systematik
from sem.quellen import digcomp as D

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "training"))
import export_hf as E  # noqa: E402


def _lo(nr: str, text: str, niveau="Basic"):
    return {"@type": "LearningOutcome", "@id": f"LearningOutcome/{nr}", "description": text, "type": "Skill",
            "ai_label": "AI-Implicit", "competence_area_id": "CompetenceArea/2",
            "competence_id": "Competence/" + nr[2:5], "four_levels_proficiency_name": f"ProficiencyLevel/{niveau}"}


def test_errata_loeschen_neu_nummerieren_und_korrigieren(tmp_path):
    graph = [{"@type": "CompetenceArea", "@id": "CompetenceArea/2", "name": "Communication", "description": "d"},
             {"@type": "Competence", "@id": "Competence/2.5", "name": "Digital behaviour", "description": "d",
              "competence_area_id": "CompetenceArea/2"},
             _lo("LO2.5.07", "Identify key rights."), _lo("LO2.5.09", "Identify key rights (duplicate)."),
             _lo("LO2.5.10", "Use a respectful tone."),
             _lo("LO2.5.15", "Respond with effective and respective communication."),
             _lo("LO2.6.27", "Stay informed about developments in relation digital identity management.")]
    p = tmp_path / "dc.jsonld"
    p.write_text(json.dumps({"@graph": graph}), encoding="utf-8")
    los = {lo["id_original"]: lo for lo in D.lese_jsonld(p)["lernergebnisse"]}
    assert "LO2.5.09" not in los  # Duplikat entfällt
    assert los["LO2.5.10"]["id"] == "LO2.5.09" and los["LO2.5.15"]["id"] == "LO2.5.14"
    assert los["LO2.5.07"]["id"] == "LO2.5.07"
    assert "respectful" in los["LO2.5.15"]["text_en"] and "respective" in los["LO2.5.15"]["text_original"]
    assert los["LO2.6.27"]["text_en"].endswith("digital identity management and protection")


def test_mitgelieferte_daten_vollstaendig():
    rows = D.laden()
    if not rows:
        pytest.skip("digcomp30.jsonl fehlt")
    los = [r for r in rows if r["typ"] == "lernergebnis"]
    assert len(los) == 522 and len({r["id"] for r in los}) == 522
    assert sum(r["typ"] == "kompetenz" for r in rows) == 21 and sum(r["typ"] == "bereich" for r in rows) == 5
    assert all(r.get("text_de") for r in los)


def test_handmapping_deckt_alle_kompetenzen_ab():
    hand = DE.fachgebiete()
    fg = {f["notation"] for f in systematik()["fachgebiete"]}
    kompetenzen = {r["id"] for r in D.laden() if r["typ"] == "kompetenz"} or set(hand["kompetenzen"])
    assert set(hand["kompetenzen"]) == kompetenzen
    assert all(1 <= len(k["fachgebiete"]) <= 2 and set(k["fachgebiete"]) <= fg for k in hand["kompetenzen"].values())
    assert set(hand["niveaus"]) == set(D.NIVEAUS)
    eb = {n["id"] for n in get("erwachsenenbildung").niveaus["niveaus"]}
    bb = {n["id"] for n in get("berufsbildung").niveaus["niveaus"]}
    assert all(v["eb_niveau"] in eb and v["berufsbildung"] in bb for v in hand["niveaus"].values())


def _seeds(bereich: str, n: int = 120) -> list[dict]:
    if not D.lernergebnisse():
        pytest.skip("digcomp30.jsonl ohne Übersetzung")
    try:
        return build_seeds(get(bereich), n, seed=3, auswahl={"quellen": ["digcomp"]})
    except FileNotFoundError:
        pytest.skip(f"Mappings für {bereich} fehlen")


def test_erwachsenenbildung_seeds():
    hand = DE.fachgebiete()
    assert "digcomp" in get("erwachsenenbildung").auswahl_optionen()["quellen"]["standard"]
    for s in _seeds("erwachsenenbildung"):
        assert s["synth:thema"]["id"].startswith(D.URI_BASIS)
        assert s["synth:digcomp_niveau"] in D.NIVEAUS and s["synth:digcomp_art"] in D.ARTEN.values()
        assert s["synth:digcomp_ki"] in D.KI.values()
        k = s["synth:digcomp_kompetenz"]["id"]
        assert s["synth:fachgebiet_vhs"][0].endswith("#fg" + hand["kompetenzen"][k]["fachgebiete"][0])
        assert s["synth:niveau"]["id"] == hand["niveaus"][s["synth:digcomp_niveau"]]["eb_niveau"]
        assert "nicht verantwortlich" in s["synth:provenance"]["synth:thema"]["quellenangabe"]


def test_berufsbildung_seeds_standard_aus():
    b = get("berufsbildung")
    assert "digcomp" not in b.auswahl_optionen()["quellen"]["standard"]
    hand = DE.fachgebiete()
    for s in _seeds("berufsbildung"):
        assert s["synth:beruf"]["uri"] and s["ccm:oeh_profession_group"]
        assert s["synth:niveau"]["id"] == hand["niveaus"][s["synth:digcomp_niveau"]]["berufsbildung"]


def test_export_digcomp_spalten():
    for bereich in ("erwachsenenbildung", "berufsbildung"):
        for s in _seeds(bereich, 30):
            row = E.flatten({**s, "cclom:title": "T", "cclom:general_description": "B", "cclom:general_keyword": ["k"],
                             "synth:volltext": "V"})
            assert row["digcomp_competence_id"] and row["digcomp_level"] and row["digcomp_ai"] in (
                "explicit", "implicit", "none")
            assert row["topic"] and row["topic_uri"].startswith(D.URI_BASIS)
