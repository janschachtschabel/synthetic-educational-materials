import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.erwachsenenbildung import F_FACHGEBIET, F_PROGRAMMBEREICH, FREMDSPRACHEN, systematik
from sem.bereiche.erwachsenenbildung.build import ziele
from sem.prompts import load_prompts, render


def _bereich():
    b = get("erwachsenenbildung")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    return b


def test_systematik_85_fachgebiete_und_ziele_ohne_sammelpositionen():
    s = systematik()
    assert len(s["programmbereiche"]) == 7 and len(s["fachgebiete"]) == 85
    z = ziele()
    assert "4.fs" in z and not any(k.endswith(".00") for k in z) and not set(FREMDSPRACHEN) & set(z)


def test_seeds_fachgebiet_niveau_und_sprache():
    b = _bereich()
    seeds = build_seeds(b, 400, "programmbereich", seed=3)
    for s in seeds:
        assert s[S.F_CONTEXT] == ["http://w3id.org/openeduhub/vocabs/educationalContext/erwachsenenbildung"]
        notation = s[F_FACHGEBIET][0].rsplit("fg", 1)[-1]
        assert notation != "4.fs" and s[F_PROGRAMMBEREICH]
        for f in (S.F_CONTEXT, F_FACHGEBIET, S.F_LRT, S.F_ROLE, S.F_THEMA):
            assert f in s[S.F_PROVENANCE]
        if notation in FREMDSPRACHEN and notation != "4.26":
            assert len(s[S.F_LANGUAGE]) == 2 and s[S.F_NIVEAU]["id"].startswith("ger_")
        if notation.startswith("7."):
            assert s["synth:sprachniveau"] == "einfache Sprache"
    assert build_seeds(b, 400, "programmbereich", seed=3) == seeds


def test_prompt_erwachsenenbildung():
    b = _bereich()
    s = build_seeds(b, 1, "thema", seed=2)[0]
    text = render(load_prompts("erwachsenenbildung")["volltext"], b.prompt_values(s))
    assert s[S.F_THEMA]["label_de"] in text and "{{" not in text
