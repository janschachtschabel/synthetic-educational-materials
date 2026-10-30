import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.hochschule import clean_label


def test_clean_label():
    assert clean_label("Studienbereich Informatik") == "Informatik"
    assert clean_label("Informations- und Bibliothekswissenschaften (nicht für Verwaltungsfachhochschulen)") == \
        "Informations- und Bibliothekswissenschaften"


def test_seeds_haben_feste_bildungsstufe_und_herkunft():
    b = get("hochschule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    seeds = build_seeds(b, 300, "thema", seed=7)
    assert len(seeds) == 300
    for s in seeds:
        assert s[S.F_CONTEXT] == ["http://w3id.org/openeduhub/vocabs/educationalContext/hochschule"]
        assert s[S.F_CONTEXT + "_DISPLAYNAME"] == ["Hochschule"]
        for f in (S.F_CONTEXT, S.F_TAXON_UNI, S.F_LRT, S.F_ROLE, S.F_THEMA, S.F_NIVEAU):
            assert f in s[S.F_PROVENANCE]
        if s[S.F_THEMA]["lehrebene"] == "forschung":
            assert s[S.F_NIVEAU]["id"] != "bachelor_einstieg"
    assert build_seeds(b, 300, "thema", seed=7) == seeds  # reproduzierbar
