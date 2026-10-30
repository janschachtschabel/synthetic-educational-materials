"""run_tasks: Fortsetzen, Lock-Datei, Wiederholung, Abbruch und Fehlerprotokoll, ohne Netz."""

import json

import httpx
import openai
import pytest

from sem import llm
from sem.llm import LLMConfig, read_done, run_tasks


@pytest.fixture(autouse=True)
def ohne_wartezeit(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def _cfg(**kw):
    return LLMConfig(**{"api_key": "test", "workers": 2, **kw})


def _zeilen(path):
    return [json.loads(z) for z in path.read_text(encoding="utf-8").splitlines() if z.strip()]


def _status_fehler(cls, code):
    req = httpx.Request("POST", "http://test/v1/chat/completions")
    return cls(f"Fehler {code}", response=httpx.Response(code, request=req), body=None)


def _tasks(n):
    return [{"id": f"t{i}"} for i in range(n)]


def test_fortsetzen_ueberspringt_erledigte(tmp_path):
    out = tmp_path / "out.jsonl"
    aufrufe = []

    def worker(client, t):
        aufrufe.append(t["id"])
        return {"id": t["id"]}

    p1 = run_tasks(_tasks(3), worker, out, _cfg(), on_progress=None)
    p2 = run_tasks(_tasks(5), worker, out, _cfg(), on_progress=None)
    assert (p1.done, p2.done, p2.skipped) == (3, 2, 3)
    assert sorted(aufrufe) == [f"t{i}" for i in range(5)]
    assert sorted(r["id"] for r in _zeilen(out)) == [f"t{i}" for i in range(5)]
    assert not out.with_suffix(".lock").exists()


def test_vorhandene_lock_datei_verhindert_zweiten_lauf(tmp_path):
    out = tmp_path / "out.jsonl"
    out.with_suffix(".lock").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lock"):
        run_tasks(_tasks(1), lambda c, t: {"id": t["id"]}, out, _cfg(), on_progress=None)


def test_unbrauchbare_antwort_wird_wiederholt_dann_protokolliert(tmp_path):
    out = tmp_path / "out.jsonl"
    versuche = {}

    def worker(client, t):
        versuche[t["id"]] = versuche.get(t["id"], 0) + 1
        if t["id"] == "t0" and versuche["t0"] < 3:
            raise ValueError("kaputtes JSON")
        if t["id"] == "t1":
            raise ValueError("immer kaputt")
        return {"id": t["id"]}

    p = run_tasks(_tasks(3), worker, out, _cfg(max_retries=4), on_progress=None)
    assert (p.done, p.failed, p.status) == (2, 1, "fertig")
    assert versuche == {"t0": 3, "t1": 4, "t2": 1}
    assert [f["id"] for f in _zeilen(out.with_suffix(".fehler.jsonl"))] == ["t1"]
    # beim nächsten Lauf wird nur die fehlgeschlagene Aufgabe erneut versucht
    assert run_tasks(_tasks(3), worker, out, _cfg(max_retries=1), on_progress=None).total == 1


def test_einzelner_bad_request_haelt_den_lauf_nicht_an(tmp_path):
    out = tmp_path / "out.jsonl"
    versuche = {}

    def worker(client, t):
        versuche[t["id"]] = versuche.get(t["id"], 0) + 1
        if t["id"] == "t1":
            raise _status_fehler(openai.BadRequestError, 400)  # z. B. Kontext zu lang
        return {"id": t["id"]}

    p = run_tasks(_tasks(6), worker, out, _cfg(), on_progress=None)
    assert (p.done, p.failed, p.status) == (5, 1, "fertig")
    assert versuche["t1"] == 1  # nicht wiederholt
    assert "BadRequestError" in _zeilen(out.with_suffix(".fehler.jsonl"))[0]["fehler"]


def test_bad_request_in_folge_pausiert_den_lauf(tmp_path):
    out = tmp_path / "out.jsonl"

    def worker(client, t):
        raise _status_fehler(openai.BadRequestError, 400)  # z. B. unbekannter Parameter: Konfigurationsfehler

    p = run_tasks(_tasks(40), worker, out, _cfg(workers=1), on_progress=None)
    assert p.status.startswith("pausiert") and p.failed == llm.MAX_BAD_REQUESTS_IN_FOLGE


def test_auth_fehler_pausiert_sofort(tmp_path):
    out = tmp_path / "out.jsonl"

    def worker(client, t):
        raise _status_fehler(openai.AuthenticationError, 401)

    p = run_tasks(_tasks(10), worker, out, _cfg(workers=1), on_progress=None)
    assert p.status.startswith("pausiert") and p.failed == 1 and p.done == 0


def test_halbe_letzte_zeile_wird_nicht_mit_neuem_datensatz_verklebt(tmp_path):
    out = tmp_path / "out.jsonl"
    out.write_text('{"id": "t0"}\n{"id": "t1", "abgebroch', encoding="utf-8")
    assert read_done(out) == {"t0"}
    run_tasks(_tasks(2), lambda c, t: {"id": t["id"]}, out, _cfg(), on_progress=None)
    assert read_done(out) == {"t0", "t1"}


def test_leser_ueberspringen_halbe_zeile(tmp_path):
    from sem import vorschau
    from sem.mapping.abstimmung import lade_stimmen

    p = tmp_path / "x.jsonl"
    p.write_text('{"id": "a", "stimme": 0, "antworten": {"o1": []}}\n{"id": "b", "stimme": 1, "antw', encoding="utf-8")
    assert [r["id"] for r in vorschau.lade(p)] == ["a"]
    assert dict(lade_stimmen(p)) == {"o1": {0: []}}


def test_env_key_nur_fuer_env_base_url():
    from sem.llm import api_key_fuer

    env = LLMConfig(base_url=None, api_key="env-key")
    assert api_key_fuer("", None, env) == "env-key"
    assert api_key_fuer("https://fremd.example/v1", None, env) is None
    assert api_key_fuer("https://fremd.example/v1", "eigener", env) == "eigener"
    env = LLMConfig(base_url="https://intern.example/v1", api_key="env-key")
    assert api_key_fuer("https://intern.example/v1", "", env) == "env-key"
