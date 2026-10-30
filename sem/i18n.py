"""Sprache der Oberfläche (Deutsch oder Englisch).

Der deutsche Text ist der Schlüssel; die englische Fassung steht in i18n/en.json. Fehlt ein Eintrag,
erscheint der deutsche Text. Texte mit Platzhaltern werden erst übersetzt und dann mit .format() gefüllt.
Neue Texte sammelt scripts/i18n_texte.py ein; fehlende Übersetzungen ergänzt es auf Wunsch per LLM.
"""

from __future__ import annotations

import json
from functools import cache

from .paths import ROOT

I18N_DIR = ROOT / "i18n"
UI_SPRACHEN = {"de": "Deutsch", "en": "English"}


@cache
def woerterbuch(sprache: str) -> dict[str, str]:
    path = I18N_DIR / f"{sprache}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def t(text: str, sprache: str = "de") -> str:
    if sprache == "de" or not text:
        return text
    return woerterbuch(sprache).get(text, text)
