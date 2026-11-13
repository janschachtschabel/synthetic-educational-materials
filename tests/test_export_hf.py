import sys
from pathlib import Path

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
            assert row["subject_uri"] and row["topic"]
            assert len(row["subject"]) == len(row["subject_uri"])
            if key != "hochschule":
                assert row["discipline_uri"] and len(row["discipline"]) == len(row["discipline_uri"])
            if key == "berufsbildung":
                assert row["occupation_uri"] and (row["competence_uri"] or s.get("synth:lernfeld"))
            if key == "elementar":
                assert row["early_childhood_area"]
            if key == "erwachsenenbildung":
                assert row["programme_area"]
