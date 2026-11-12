import json

import pytest

from sem import schema as S
from sem.beschreibung_einstieg import (F_BESCHREIBUNG_ALT, beschreibung_einstieg, einstieg_ersetzen, parse_satz,
                                       teilen)
from sem.generate import beschreibung_floskel
from sem.llm import LLMConfig
from sem.paths import PROMPT_DIR
from sem.prompts import load_prompts

REST = ("Lernende vergleichen Brennweiten an einfachen Beispielen. Ein Versuch mit einer Lupe zeigt den Strahlengang. "
        "Aufgaben mit Lösungen sichern das Gelernte. Das eignet sich für die Sekundarstufe I und für Vertretungsstunden.")
ALT = "Im Mittelpunkt steht die Frage, wie eine Sammellinse ein Bild erzeugt. " + REST


def _rec(beschreibung=ALT, i=0):
    return {"id": f"m{i}", S.F_BEREICH: "schule", S.F_TITLE: "Wie eine Linse ein Bild erzeugt",
            S.F_DESCRIPTION: beschreibung, S.F_FULLTEXT: "x", S.F_GENERATION: {"modell": "alt"}}


class Client:
    def __init__(self, antworten):
        self.antworten, self.anfragen = list(antworten), []

    def chat(self, system, user, json_mode=False):
        self.anfragen.append(user)
        return self.antworten.pop(0)


def _ersetzen(rec, antworten):
    vorlage = (PROMPT_DIR / "beschreibung_einstieg.md").read_text(encoding="utf-8")
    client = Client(antworten)
    meta = {"modell": "fake", "datum": "2026-11-12", "prompt_hash": "abc"}
    return einstieg_ersetzen(client, rec, {"schule": load_prompts("schule")}, vorlage, meta), client


def test_floskeln_und_teilen():
    assert beschreibung_floskel(ALT) == "Im Mittelpunkt"
    assert beschreibung_floskel("Das Material zeigt …") == "Das Material"
    assert beschreibung_floskel("Sammellinsen bündeln Licht.") is None
    erster, rest = teilen(ALT)
    assert erster.startswith("Im Mittelpunkt") and rest == REST


def test_parse_satz():
    assert parse_satz("„Sammellinsen erzeugen Bilder, deren Lage von der Brennweite abhängt.“") == \
        "Sammellinsen erzeugen Bilder, deren Lage von der Brennweite abhängt."
    for kaputt in ("Im Mittelpunkt steht die Linse.", "Das Arbeitsblatt behandelt Sammellinsen und ihre Bilder.",
                   "Linsen.", "Sammellinsen bündeln Licht. Zerstreuungslinsen nicht.", "Sammellinsen bündeln Licht"):
        with pytest.raises(ValueError):
            parse_satz(kaputt)


def test_einstieg_ersetzen():
    rec, client = _ersetzen(_rec(), ["Das Material zeigt Linsen.", "Wie erzeugt eine Sammellinse ein Bild?"])
    assert rec[S.F_DESCRIPTION] == "Wie erzeugt eine Sammellinse ein Bild? " + REST
    assert rec[F_BESCHREIBUNG_ALT] == ALT and rec[S.F_FULLTEXT] == "x"
    info = rec[S.F_GENERATION]["beschreibung_einstieg"]
    assert info["neu_angefragt"] == 1 and info["floskel"] == "Im Mittelpunkt" and rec[S.F_GENERATION]["modell"] == "alt"
    assert "Bisheriger erster Satz: Im Mittelpunkt steht die Frage" in client.anfragen[0]
    assert REST in client.anfragen[0]


def test_ohne_floskel_unveraendert_und_behalten():
    rec = _rec("Sammellinsen erzeugen Bilder. " + REST)
    neu, client = _ersetzen(rec, [])
    assert neu is rec and not client.anfragen
    neu, _ = _ersetzen(_rec(), ["Im Mittelpunkt steht x."] * 3)
    assert neu[S.F_DESCRIPTION] == ALT and "behalten" in neu[S.F_GENERATION]["beschreibung_einstieg"]
    assert F_BESCHREIBUNG_ALT not in neu


def test_ganze_datei(tmp_path, monkeypatch):
    src = tmp_path / "m.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in
                             [_rec(i=0), _rec("Sammellinsen erzeugen Bilder. " + REST, i=1)]) + "\n", encoding="utf-8")

    class Fake:
        def __init__(self, cfg):
            pass

        def chat(self, system, user, json_mode=False):
            return "Sammellinsen erzeugen Bilder, deren Lage von der Brennweite abhängt."

    monkeypatch.setattr("sem.llm.LLMClient", Fake)
    out = tmp_path / "o.jsonl"
    prog = beschreibung_einstieg(src, out, LLMConfig(api_key="x", workers=1), on_progress=None)
    recs = {r["id"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}
    assert prog.done == 2 and recs["m0"][S.F_DESCRIPTION].startswith("Sammellinsen erzeugen")
    assert recs["m1"][S.F_DESCRIPTION] == "Sammellinsen erzeugen Bilder. " + REST
