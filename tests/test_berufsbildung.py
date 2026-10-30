import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.berufsbildung import Berufsbildung
from sem.bereiche.berufsbildung import build as B
from sem.mapping import pruefung
from sem.prompts import load_prompts, render
from sem.vocabs import load as load_vocab


def _bereich():
    b = get("berufsbildung")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Berufe gebaut")
    return b


def test_kldb_vokabular_hat_vier_ebenen_und_kennziffern():
    voc = load_vocab("kldb")
    ug = B.kldb_untergruppen(voc)
    assert len(ug) > 600 and all(len(c["notation"]) == 4 for c in ug)
    abgeleitet = B.kldb_ableiten(voc, [ug[0]["uri"]])
    assert [len(abgeleitet[k][0]["notation"]) for k in ("berufsuntergruppe", "berufsgruppe", "berufshauptgruppe",
                                                         "berufsbereich")] == [4, 3, 2, 1]


def test_wlo_fach_passt_auf_ebene_berufsgruppe():
    beruf = {"berufsuntergruppe": [{"notation": "2932"}]}
    assert Berufsbildung.wlo_fach_passt({"belege": {"wlo": {"kldb": {"2931": 4}}}}, beruf)
    assert not Berufsbildung.wlo_fach_passt({"belege": {"wlo": {"kldb": {"2441": 4}}}}, beruf)
    assert not Berufsbildung.wlo_fach_passt({}, beruf)


def test_korrekturen_mit_eigener_spalte(tmp_path):
    p = tmp_path / "korrekturen.csv"
    p.write_text("id,korrektur_codes,korrektur_qualifizierung,kommentar\n"
                 "http://data.europa.eu/esco/occupation/x,2932,ausbildung,ok\n"
                 "http://data.europa.eu/esco/occupation/y,-,,raus\n", encoding="utf-8")
    k = pruefung.lade_korrekturen(p, ebene_spalte="korrektur_qualifizierung")
    assert k["http://data.europa.eu/esco/occupation/x"]["codes"] == ["2932"]
    assert k["http://data.europa.eu/esco/occupation/x"]["lehrebene"] == "ausbildung"
    assert k["http://data.europa.eu/esco/occupation/y"]["ausschliessen"]


def test_seeds_haben_beruf_kompetenz_und_herkunft():
    b = _bereich()
    seeds = build_seeds(b, 300, "beruf", seed=7)
    assert len(seeds) == 300
    for s in seeds:
        assert s[S.F_BEREICH] == "berufsbildung"
        assert s[S.F_CONTEXT][0].rsplit("/", 1)[-1] in ("berufliche_bildung", "fortbildung")
        assert s[S.F_BERUF]["uri"].startswith("http://data.europa.eu/esco/occupation/")
        assert s[S.F_KOMPETENZ]["uri"].startswith("http://data.europa.eu/esco/skill/")
        assert all("/vocabs/kldb/" in u for u in s[S.F_PROFESSION])
        for f in (S.F_CONTEXT, S.F_PROFESSION, S.F_LRT, S.F_ROLE, S.F_THEMA, S.F_NIVEAU, S.F_BERUF, S.F_KOMPETENZ):
            assert f in s[S.F_PROVENANCE]
        # Standardauswahl: Ausbildungs- und Fortbildungsberufe, essenzielle Kompetenzen, keine Querschnittskompetenzen
        assert s[S.F_BERUF]["qualifizierung"] in ("ausbildung", "fortbildung")
        assert s[S.F_KOMPETENZ]["relation"] == "essenziell"
        assert s[S.F_KOMPETENZ].get("reichweite") != "querschnitt"
        if s[S.F_BERUF]["qualifizierung"] == "ausbildung":
            assert s[S.F_NIVEAU]["dqr"] in (3, 4)
    assert build_seeds(b, 300, "beruf", seed=7) == seeds  # reproduzierbar


def test_prompt_enthaelt_beruf_und_kompetenz():
    b = _bereich()
    s = build_seeds(b, 1, "beruf", seed=3)[0]
    values = b.prompt_values(s)
    text = render(load_prompts("berufsbildung")["volltext"], values)
    assert s[S.F_BERUF]["label"] in text and s[S.F_KOMPETENZ]["label"] in text
    assert "{{" not in text
