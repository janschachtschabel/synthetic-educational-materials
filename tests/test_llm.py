"""run_tasks: Fortsetzen, Lock-Datei, Wiederholung, Abbruch und Fehlerprotokoll, ohne Netz."""

import json

import openai
import pytest

try:  # openai ab 3.x baut auf httpx2, ältere Fassungen auf httpx
    import httpx2 as httpx
except ImportError:
    import httpx

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


def test_flex_ohne_kapazitaet_faellt_auf_standard_zurueck(monkeypatch):
    from types import SimpleNamespace

    from sem.llm import LLMClient

    monkeypatch.setattr(llm, "FLEX_WARTEN", 0)
    client = LLMClient(LLMConfig(api_key="test", service_tier="flex"))
    gesendet = []

    def create(**params):
        gesendet.append(params.get("service_tier"))
        if params.get("service_tier") == "flex":
            raise _status_fehler(openai.RateLimitError, 429)  # Meldung enthält „Fehler 429“, nicht „flex“
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(client.client.chat.completions, "create", create)
    with pytest.raises(openai.RateLimitError):  # gewöhnliches Rate-Limit: kein Rückfall, run_tasks wiederholt
        client.chat("s", "u")
    gesendet.clear()

    def flex_voll(**params):
        gesendet.append(params.get("service_tier"))
        if params.get("service_tier") == "flex":
            req = httpx.Request("POST", "http://test/v1/chat/completions")
            raise openai.RateLimitError("Flex does not have sufficient resources", response=httpx.Response(429, request=req),
                                        body=None)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(client.client.chat.completions, "create", flex_voll)
    assert client.chat("s", "u") == "ok"
    assert gesendet == ["flex"] * llm.FLEX_VERSUCHE + [None]
    assert client.zaehler()["aufrufe_standard"] == 1


def test_verwaiste_sperre_wird_uebernommen(tmp_path):
    import os
    import socket
    import subprocess
    import sys

    from sem.llm import sperre_nehmen

    lock = tmp_path / "out.lock"
    tot = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    lock.write_text(f"{tot.stdout.strip()} {socket.gethostname()}", encoding="utf-8")  # Prozess ist beendet
    sperre_nehmen(lock)
    assert lock.read_text(encoding="utf-8").split()[0] == str(os.getpid())
    with pytest.raises(RuntimeError, match="existiert"):  # eigener, laufender Prozess: Sperre bleibt
        sperre_nehmen(lock)
    lock.write_text("", encoding="utf-8")  # alte Sperrdatei ohne Angaben: nicht anfassen
    with pytest.raises(RuntimeError):
        sperre_nehmen(lock)


def test_anhalten_stellt_keine_neue_anfrage_mehr(tmp_path, monkeypatch):
    """Nach dem Anhalten bricht ein Material vor seiner nächsten Anfrage ab, statt erst fertig zu laufen."""
    import threading
    from types import SimpleNamespace

    stop = threading.Event()
    gesendet = []

    def create(**params):
        gesendet.append(1)
        if len(gesendet) == 2:
            stop.set()  # Anhalten mitten im zweiten Material
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    def worker(client, t):
        if not hasattr(client.client.chat.completions, "_fake"):
            monkeypatch.setattr(client.client.chat.completions, "create", create)
            client.client.chat.completions._fake = True
        for _ in range(3):  # drei Anfragen je Material
            client.chat("s", "u")
        return {"id": t["id"]}

    stand = []
    p = run_tasks(_tasks(5), worker, tmp_path / "out.jsonl", _cfg(workers=1), stop_event=stop,
                  on_progress=lambda pr: stand.append((pr.anfragen, pr.status)))
    assert len(gesendet) == 2 and p.done == 0 and p.failed == 0
    assert p.status.startswith("pausiert")
    assert stand[0][0] == 1  # Fortschritt schon nach der ersten Anfrage gemeldet
