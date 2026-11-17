"""Vorgaben für den Volltext: Sprachprofil je Niveau, Einstiege für Transkripte, Zielsprache nur mit passendem Fach."""

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.schule import zielsprache_gilt
from sem.prompts import load_prompts, render

ROLE = "http://w3id.org/openeduhub/vocabs/intendedEndUserRole/"


def test_sprachprofil_nur_fuer_lernende():
    b = get("schule")
    seed = {S.F_NIVEAU: {"id": "klasse_1_2"}, S.F_ROLE: [ROLE + "learner"]}
    assert "höchstens 8 Wörter" in b.sprachprofil(seed)
    assert b.sprachprofil({**seed, S.F_ROLE: [ROLE + "teacher"]}) == ""
    assert b.sprachprofil({S.F_NIVEAU: {"id": "gibt_es_nicht"}, S.F_ROLE: [ROLE + "learner"]}) == ""


def test_jedes_niveau_hat_ein_sprachprofil():
    import json

    from sem.paths import MAPPING_DIR

    for bereich in ("schule", "hochschule", "berufsbildung", "elementar", "erwachsenenbildung"):
        daten = json.loads((MAPPING_DIR / bereich / "niveaus.json").read_text(encoding="utf-8"))
        assert all(n.get("sprachprofil") for n in daten["niveaus"]), bereich


def test_sprachprofil_im_prompt():
    for bereich in ("schule", "hochschule", "berufsbildung", "elementar", "erwachsenenbildung"):
        p = load_prompts(bereich)["volltext"]
        assert "Sprache und Lesbarkeit: kurz." in render(p, {"sprachprofil": "kurz"}), bereich
        assert "Sprache und Lesbarkeit" not in render(p, {}), bereich


def test_einstiege_fuer_transkripte():
    b = get("hochschule")
    for i in range(20):
        assert b.stil(f"x-{i}", "Audio-Transkript")["einstieg"] in b.einstiege_transkript
        assert b.stil(f"x-{i}", "Text")["einstieg"] in b.einstiege


def test_zielsprache_nur_mit_passendem_fach():
    assert zielsprache_gilt("Englisch", ["Englisch"])
    assert zielsprache_gilt("Englisch", [])
    assert not zielsprache_gilt("Englisch", ["Berufliche Bildung"])
    assert not zielsprache_gilt("Physik", ["Physik"])
