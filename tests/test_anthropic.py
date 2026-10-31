"""Anthropic als Anbieter: Parameter, sofortige Anfragen mit Workern, Fehlerklassen, Batch, Oberfläche, ohne Netz."""

import re
from types import SimpleNamespace

import anthropic
import pytest

try:
    import httpx2 as httpx
except ImportError:
    import httpx

from sem import batch as B
from sem import llm, prozess
from sem.llm import LLMClient, LLMConfig, read_done, run_tasks
from sem.prompts import load_prompts
from tests.test_batch import Antworten, _zeilen, seeds_datei  # noqa: F401 - Fixture
from tests.test_web import HX, _lauf, client, ordner  # noqa: F401 - Fixtures
from sem.web import app as web


def _cfg(**kw):
    return LLMConfig(**{"anbieter": "anthropic", "api_key": "test", "model": "claude-haiku-5-5", "workers": 3, **kw})


def _nachricht(text, stop="end_turn", **usage):
    u = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
         **usage}
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, usage=SimpleNamespace(**u, output_tokens_details=None))


def _status_fehler(cls, code, text="Fehler"):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(text, response=httpx.Response(code, request=req), body=None)


def test_parameter_mit_cache_effort_und_ohne_temperature():
    p = _cfg(reasoning_effort="minimal", temperature=0.3).parameter("System", "Nutzer", json_mode=True)
    assert p["model"] == "claude-haiku-5-5" and p["max_tokens"] == llm.ANTHROPIC_MAX_TOKENS
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["system"][0]["text"].startswith("System") and llm.ANTHROPIC_JSON_HINWEIS in p["system"][0]["text"]
    assert p["messages"] == [{"role": "user", "content": "Nutzer"}]
    assert p["output_config"] == {"effort": "low"}  # minimal gibt es bei Anthropic nicht
    assert "temperature" not in p and "response_format" not in p and "service_tier" not in p
    assert "output_config" not in _cfg(reasoning_effort=None).parameter("S", "U")


def test_umgebung_und_flex_nicht_erlaubt(monkeypatch):
    monkeypatch.setenv("LLM_ANBIETER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    cfg = LLMConfig.from_env()
    assert (cfg.anbieter, cfg.api_key, cfg.model) == ("anthropic", "sk-ant", "claude-haiku-5-5")
    assert cfg.public()["base_url"] == "https://api.anthropic.com" and cfg.public()["anbieter"] == "anthropic"
    assert LLMConfig.from_env(anbieter="openai").api_key == "sk-oai"
    with pytest.raises(ValueError, match="Flex gibt es bei Anthropic nicht"):
        LLMClient(_cfg(service_tier="flex"))


def test_sofort_mit_mehreren_workern_zaehlt_tokens(tmp_path, monkeypatch):
    client_cfg = _cfg()
    gesehen = []

    def create(**params):
        gesehen.append(params)
        return _nachricht('Hier: {"ok": 1}', input_tokens=3, cache_read_input_tokens=100,
                          cache_creation_input_tokens=7)

    orig = LLMClient.__init__

    def init(self, cfg):
        orig(self, cfg)
        assert isinstance(self.client, anthropic.Anthropic)
        monkeypatch.setattr(self.client.messages, "create", create)

    monkeypatch.setattr(LLMClient, "__init__", init)

    def worker(c, t):
        z = c.zaehler(neu=True)
        return {"id": t["id"], "antwort": c.chat_json("S", "U"), "tokens": dict(z)}

    prog = run_tasks([{"id": f"t{i}"} for i in range(6)], worker, tmp_path / "o.jsonl", client_cfg, on_progress=None)
    assert prog.done == 6 and len(gesehen) == 6
    zeile = _zeilen(tmp_path / "o.jsonl")[0]
    assert zeile["antwort"] == {"ok": 1}  # JSON auch mit Satz davor
    assert zeile["tokens"]["eingabe"] == 110 and zeile["tokens"]["davon_cache"] == 100


def test_ablehnung_wird_wiederholt():
    with pytest.raises(ValueError, match="refusal"):
        llm.anthropic_text(_nachricht("", stop="refusal"))


def test_fehlerklassen_von_anthropic():
    assert llm._is_connection_error(_status_fehler(anthropic.InternalServerError, 529))
    assert llm._is_connection_error(anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")))
    assert llm._is_fatal(_status_fehler(anthropic.AuthenticationError, 401))
    guthaben = _status_fehler(anthropic.BadRequestError, 400, "Your credit balance is too low to access the API")
    assert llm._is_fatal(guthaben)
    einzeln = _status_fehler(anthropic.BadRequestError, 400, "prompt is too long")
    assert not llm._is_fatal(einzeln) and llm._is_bad_request(einzeln)
    assert not llm._is_fatal(_status_fehler(anthropic.RateLimitError, 429))


class FakeAnthropic:
    """messages.batches.create/retrieve/results; ein Batch ist beim ersten retrieve fertig."""

    def __init__(self, antworten, ablaufen=1, fehler=1):
        self.antworten, self._batches, self.anfragen = antworten, {}, []
        self.ablaufen, self.fehler = ablaufen, fehler  # so viele Anfragen laufen ab bzw. scheitern einmal
        batches = SimpleNamespace(create=self._anlegen, retrieve=self._abrufen, results=self._ergebnisse)
        self.messages = SimpleNamespace(batches=batches)

    def _anlegen(self, requests, timeout=None):
        bid = f"msgbatch_{len(self._batches)}"
        self._batches[bid] = list(requests)
        return SimpleNamespace(id=bid, processing_status="in_progress")

    def _abrufen(self, bid):
        return SimpleNamespace(id=bid, processing_status="ended")

    def _ergebnisse(self, bid, timeout=None):
        for r in self._batches[bid]:
            self.anfragen.append(r)
            if self.ablaufen:
                self.ablaufen -= 1
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="expired"))
                continue
            if self.fehler:
                self.fehler -= 1
                err = SimpleNamespace(type="error", error=SimpleNamespace(type="api_error", message="kaputt"))
                yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="errored", error=err))
                continue
            text = self.antworten(r["params"]["messages"][0]["content"])
            yield SimpleNamespace(custom_id=r["custom_id"],
                                  result=SimpleNamespace(type="succeeded", message=_nachricht(text)))


def test_batch_bei_anthropic(seeds_datei, tmp_path):  # noqa: F811
    path, seeds = seeds_datei
    fake = FakeAnthropic(Antworten(load_prompts("hochschule"), titel_kaputt=0))
    lauf = B.BatchLauf(path, tmp_path / "materialien_t.jsonl", _cfg(), client=fake)
    for _ in range(12):
        if lauf.weiter()["abgeschlossen"]:
            break
    out = _zeilen(tmp_path / "materialien_t.jsonl")
    assert len(read_done(tmp_path / "materialien_t.jsonl")) == 3
    assert not (tmp_path / "materialien_t.fehler.jsonl").exists()  # Ablauf und ein Fehler werden neu gestellt
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", r["custom_id"]) for r in fake.anfragen)
    assert all("cache_control" in r["params"]["system"][0] for r in fake.anfragen)
    g = out[0]["synth:generierung"]
    assert g["verarbeitung"] == "batch" and g["base_url"] == "https://api.anthropic.com" and g["anbieter"] == "anthropic"
    assert lauf.stand["parameter"]["anbieter"] == "anthropic"
    # derselbe Ausgabename mit OpenAI passt nicht zu diesem Lauf
    with pytest.raises(ValueError, match="anderen Einstellungen"):
        B.BatchLauf(path, tmp_path / "materialien_t.jsonl", LLMConfig(api_key="x", model="claude-haiku-5-5"),
                    client=fake)


def test_prozess_bekommt_anthropic_key():
    args, env = prozess.llm_umgebung(_cfg(api_key="sk-ant", workers=9))
    assert env["LLM_ANBIETER"] == "anthropic" and env["ANTHROPIC_API_KEY"] == "sk-ant"
    assert env["ANTHROPIC_BASE_URL"] == "" and "OPENAI_API_KEY" not in env and "--flex" not in args


def test_oberflaeche_anthropic_waehlen(client, ordner, monkeypatch):  # noqa: F811
    out, gestartet = ordner
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_ANBIETER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/einstellungen", headers=HX, data={"anbieter": "anthropic", "model": "gpt-6-luna",
                                                       "api_key": "sk-ant", "flex": "true", "workers": "40"})
    assert r.status_code == 204
    cfg = web.llm_cfg()
    assert (cfg.anbieter, cfg.model, cfg.api_key, cfg.service_tier) == ("anthropic", "claude-haiku-5-5", "sk-ant", None)
    seite = client.get("/einstellungen").text
    assert "Flex gibt es bei Anthropic nicht" in seite and "ANTHROPIC_API_KEY" in seite
    assert 'id="e-flex"' not in seite
    assert 'value="flex"' not in client.get("/materialien").text
    # ein alter OpenAI-Lauf wird mit OpenAI fortgesetzt, nicht mit dem Anbieter der Einstellungen
    _lauf(out)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    client.post("/materialien/fortsetzen/materialien_test.jsonl", headers=HX,
                data={"aktion": "fortsetzen", "verarbeitung": "sofort", "workers": "4"})
    cfg = gestartet[-1][2]
    assert (cfg.anbieter, cfg.model, cfg.api_key) == ("openai", "m1", "sk-oai")
    web._api_key.clear()
