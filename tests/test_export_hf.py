import sys
from pathlib import Path

import pytest

from sem.bereiche import BEREICHE, get
from sem.bereiche.base import build_seeds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "training"))
import export_hf as E  # noqa: E402


def _material(seed: dict) -> dict:
    return {**seed, "cclom:title": "T", "cclom:general_description": "B", "cclom:general_keyword": ["k"],
            "synth:volltext": "V", "synth:frage_antwort_paare": []}


def test_jedes_seed_feld_wird_exportiert_oder_bewusst_weggelassen():
    """Vom Seed zum Datensatz: kein klassifizierendes Feld darf unbemerkt verloren gehen."""
    bekannt = set(E.EXPORTED_SEED_FIELDS) | set(E.NOT_EXPORTED_SEED_FIELDS)
    for key in BEREICHE:
        b = get(key)
        try:
            seeds = build_seeds(b, 400, seed=2)
        except FileNotFoundError:
            continue
        felder = {f for s in seeds for f in s if not f.endswith("_DISPLAYNAME")}
        assert not felder - bekannt, f"{key}: Seed-Felder ohne Export-Entscheidung {sorted(felder - bekannt)}"


def test_export_hat_alle_spalten_und_kontrollierte_werte():
    namen = {n for n, _ in E.COLUMNS}
    for key in BEREICHE:
        b = get(key)
        try:
            seeds = build_seeds(b, 200, seed=4)
        except FileNotFoundError:
            continue
        for s in seeds:
            row = E.flatten(_material(s))
            assert set(row) == namen
            assert row["educational_context_uri"] and row["content_type_uri"] and row["target_group_uri"]
            # Themen aus nur lokal gehaltenen Quellen (Lernfelder, Rahmencurricula; nur mit data/lokal/) gehen
            # bewusst ohne Themenlabel in den Export
            tid = s["synth:thema"]["id"]
            offen = tid.startswith(E.OPEN_TOPIC_PREFIXES) and "kmk:" not in tid or "#thema-" in tid                 or tid.startswith(E.OWN_TOPIC_PREFIXES)
            assert row["subject_uri"] and (row["topic"] or not offen)
            assert len(row["subject"]) == len(row["subject_uri"])
            if key != "hochschule":
                assert row["discipline_uri"] and len(row["discipline"]) == len(row["discipline_uri"])
            if key == "berufsbildung":
                assert row["occupation_uri"] and (row["competence_uri"] or s.get("synth:lernfeld"))
            if key == "elementar":
                assert row["early_childhood_area"]
            if key == "erwachsenenbildung":
                assert row["programme_area"]


def test_export_mit_wlo_themen(ohne_lokal):
    """Seeds aus der WLO-Abstraktion: alle Felder haben eine Export-Entscheidung, das Thema geht als Label mit."""
    import json

    from sem import wlo_seeds as WS
    from sem.bereiche.base import WLO_THEMA_PREFIX
    from test_wlo_themen import ROWS

    b = get("schule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Schul-Themen gebaut")
    p = ohne_lokal / "wlo_abstraktion" / "abstraktion.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8")
    WS.bauen([p], gnd={}, vorhanden={"schule": [], "hochschule": []}, on_progress=lambda *_: None)
    bekannt = set(E.EXPORTED_SEED_FIELDS) | set(E.NOT_EXPORTED_SEED_FIELDS)
    for key, auswahl in (("schule", {"herkunft": ["wlo_abstraktion"]}), ("hochschule", {"quellen": ["wlo_abstraktion"]})):
        bb = get(key)
        try:
            seeds = build_seeds(bb, 20, seed=3, auswahl=auswahl)
        except FileNotFoundError:
            continue
        for s in seeds:
            assert not {f for f in s if not f.endswith("_DISPLAYNAME")} - bekannt
            row = E.flatten({**s, "cclom:title": "T", "cclom:general_description": "B", "cclom:general_keyword": [],
                             "synth:volltext": "V"})
            assert row["topic"] == s["synth:thema"]["label_de"] and row["topic_uri"] is None
            assert row["subject_uri"] and s["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)
