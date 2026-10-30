"""Lokale Daten (data/lokal/): Alles läuft mit und ohne sie; Seeds und Statistik vermerken, welche genutzt wurden."""

import json

import pytest
from conftest import lokal_vorhanden

from sem import schema as S
from sem import seeds as seedlib
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.bereiche.berufsbildung.lernfelder import PLAENE
from sem.bereiche.erwachsenenbildung import DIR as EB_DIR
from sem.bereiche.erwachsenenbildung import LOKALE_HERKUNFT, THEMEN_LOKAL
from sem.bereiche.schule import bildungsstandards as bista
from sem.paths import LOKALE_DATEIEN, lokale_daten


def _bereich(key):
    b = get(key)
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    return b


def test_kennung_der_lokalen_dateien(ohne_lokal):
    assert lokale_daten() == dict.fromkeys(LOKALE_DATEIEN)
    assert lokale_daten("hochschule") == {}
    p = ohne_lokal / THEMEN_LOKAL
    p.parent.mkdir(parents=True)
    p.write_text("{}\n", encoding="utf-8")
    kennung = lokale_daten("erwachsenenbildung")[THEMEN_LOKAL]
    assert len(kennung) == 12 and kennung == lokale_daten()[THEMEN_LOKAL]


def test_repo_ohne_themen_aus_geschuetzten_gliederungen():
    rows = [json.loads(z) for z in (EB_DIR / "themen_erwachsenenbildung.jsonl").read_text(encoding="utf-8").splitlines()
            if z.strip()]
    assert rows and not [r["id"] for r in rows if r.get("herkunft") in LOKALE_HERKUNFT]
    assert not (EB_DIR / "sprachkurs_themen.json").exists()


def test_schule_ohne_lokal(ohne_lokal):
    b = _bereich("schule")
    with pytest.warns(UserWarning, match="bildungsstandards_gliederung"):
        seeds = build_seeds(b, 300, "portal", seed=4)
    assert seeds and not [s["id"] for s in seeds if S.F_BILDUNGSSTANDARDS in s]
    assert seeds[0]["synth:seed_parameter"]["lokale_daten"] == {"schule/bildungsstandards_gliederung.json": None}


def test_bista_schritte_ohne_gliederung_lassen_zuordnungen_stehen(ohne_lokal):
    vorher = bista.ZUORDNUNG_PATH.read_bytes() if bista.ZUORDNUNG_PATH.exists() else None
    for schritt in (bista.build_bista, bista.zusammenfuehren):
        with pytest.raises(FileNotFoundError, match="bildungsstandards_gliederung"):
            schritt(None)
    assert (bista.ZUORDNUNG_PATH.read_bytes() if bista.ZUORDNUNG_PATH.exists() else None) == vorher


def test_berufsbildung_ohne_lokal(ohne_lokal):
    b = get("berufsbildung")
    with pytest.warns(UserWarning, match="rahmenlehrplaene"):
        try:
            themen = b.topics()
        except FileNotFoundError:
            pytest.skip("keine Berufe gebaut")
    assert themen and not [t["id"] for t in themen if t.get("quelle") == "lernfeld"]
    seeds = build_seeds(b, 100, "beruf", seed=3)
    assert seeds and not [s["id"] for s in seeds if S.F_LERNFELD in s]
    assert seeds[0]["synth:seed_parameter"]["lokale_daten"] == {PLAENE: None}
    with pytest.raises(ValueError, match="Keine Themen"):
        build_seeds(b, 10, "beruf", seed=3, auswahl={**b.standard_auswahl(), "quellen": ["lernfeld"]})


def test_erwachsenenbildung_ohne_lokal(ohne_lokal):
    b = get("erwachsenenbildung")
    with pytest.warns(UserWarning, match="themen_lokal"):
        try:
            themen = b.topics()
        except FileNotFoundError:
            pytest.skip("keine Themen gebaut")
    assert themen and not [t["id"] for t in themen if t.get("herkunft") in LOKALE_HERKUNFT]
    seeds = build_seeds(b, 200, seed=5)
    assert len(seeds) == 200 and seeds[0]["synth:seed_parameter"]["lokale_daten"] == {THEMEN_LOKAL: None}


def test_statistik_vermerkt_fehlende_lokale_dateien(ohne_lokal, tmp_path):
    b = _bereich("schule")
    with pytest.warns(UserWarning):
        data = build_seeds(b, 20, "portal", seed=1)
    st = seedlib.write(data, tmp_path / "seeds_t.jsonl", {})
    assert st["lokale_daten"] == {"schule/bildungsstandards_gliederung.json": None}


@pytest.mark.skipif(not lokal_vorhanden("schule/bildungsstandards_gliederung.json"),
                    reason="bildungsstandards_gliederung.json liegt nur lokal (data/lokal/)")
def test_schule_mit_lokal():
    bista.gliederung.cache_clear()
    seeds = build_seeds(_bereich("schule"), 300, "portal", seed=4)
    assert [s for s in seeds if S.F_BILDUNGSSTANDARDS in s]
    assert seeds[0]["synth:seed_parameter"]["lokale_daten"]["schule/bildungsstandards_gliederung.json"]


@pytest.mark.skipif(not lokal_vorhanden(THEMEN_LOKAL), reason="themen_lokal.jsonl liegt nur lokal (data/lokal/)")
def test_erwachsenenbildung_mit_lokal():
    themen = _bereich("erwachsenenbildung").topics()
    assert {t.get("herkunft") for t in themen} >= set(LOKALE_HERKUNFT)
