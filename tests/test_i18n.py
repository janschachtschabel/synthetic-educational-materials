import json
import re

from sem.i18n import I18N_DIR, t


def test_platzhalter_gleich():
    en = json.loads((I18N_DIR / "en.json").read_text(encoding="utf-8"))
    for de, uebersetzt in en.items():
        assert sorted(re.findall(r"\{\w+\}", de)) == sorted(re.findall(r"\{\w+\}", uebersetzt)), de


def test_rueckfall_auf_deutsch():
    assert t("Seeds erzeugen", "de") == "Seeds erzeugen"
    assert t("gibt es nicht 123", "en") == "gibt es nicht 123"
    assert t("Anhalten", "en") == "Stop"
