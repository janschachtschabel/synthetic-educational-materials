import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.schule.build import wlo_stufen
from sem.prompts import load_prompts, render

KONTEXT = {"grundschule": "primar", "sekundarstufe_1": "sek1", "sekundarstufe_2": "sek2"}


def _bereich():
    b = get("schule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    return b


def test_wlo_stufen_anteil_und_mindestzahl():
    assert wlo_stufen({"datensaetze": 10, "stufen": {"sek1": 8, "sek2": 1, "primar": 3}}) == ["primar", "sek1"]
    assert wlo_stufen({"datensaetze": 2, "stufen": {"sek1": 2}}) == []
    assert wlo_stufen(None) == []


def test_seeds_haben_stufe_als_metadatum():
    b = _bereich()
    seeds = build_seeds(b, 300, "portal", seed=4)
    for s in seeds:
        stufe = KONTEXT[s[S.F_CONTEXT][0].rsplit("/", 1)[-1]]
        assert stufe in s[S.F_THEMA]["stufen"]
        assert s[S.F_CURRICULUM][0].startswith("http://w3id.org/openeduhub/vocabs/oeh-topics/")
        for f in (S.F_CONTEXT, S.F_CURRICULUM, S.F_LRT, S.F_ROLE, S.F_THEMA, S.F_NIVEAU):
            assert f in s[S.F_PROVENANCE]
    assert build_seeds(b, 300, "portal", seed=4) == seeds


def test_filter_nur_sekundarstufe_2():
    b = _bereich()
    seeds = build_seeds(b, 200, "portal", seed=9, auswahl={"stufen": ["sek2"]})
    assert {s[S.F_CONTEXT][0].rsplit("/", 1)[-1] for s in seeds} == {"sekundarstufe_2"}


def test_prompt_schule():
    b = _bereich()
    s = build_seeds(b, 1, "thema", seed=2)[0]
    text = render(load_prompts("schule")["volltext"], b.prompt_values(s))
    assert s[S.F_THEMA]["label_de"] in text and "{{" not in text
