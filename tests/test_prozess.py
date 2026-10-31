"""Läufe als eigener Prozess (sem/prozess.py): starten, Stand lesen, anhalten; gegen einen nachgebauten LLM-Server."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sem import prozess
from sem.llm import LLMConfig, Progress
from sem.prompts import load_prompts

from test_batch import Antworten, seeds_datei  # noqa: F401 (Fixture)


@pytest.fixture
def llm_server():
    """OpenAI-kompatibler Server auf localhost mit gültigen Antworten je Feld."""
    antworten = Antworten(load_prompts("hochschule"), titel_kaputt=0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = antworten(body["messages"][-1]["content"])
            out = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                              "choices": [{"index": 0, "finish_reason": "stop",
                                           "message": {"role": "assistant", "content": text}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def _warten_bis_ende(name, ordner, sekunden=90):
    ende = time.time() + sekunden
    while prozess.laeuft(name, ordner) and time.time() < ende:
        time.sleep(0.2)
    return prozess.stand(name, ordner)


def _starten(name, argv, cfg, ordner):
    args, env = prozess.llm_umgebung(cfg)
    prozess.starten(name, argv + args, {**env, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
                    ordner=ordner)


def test_lauf_als_prozess_schreibt_stand_und_materialien(tmp_path, seeds_datei, llm_server):  # noqa: F811
    seeds, _ = seeds_datei
    out = tmp_path / "materialien_t.jsonl"
    cfg = LLMConfig(base_url=llm_server, api_key="sk-geheim", model="m", workers=2)
    _starten("mat", ["materialien", "--seeds", str(seeds), "--out", str(out)], cfg, tmp_path / "p")
    s = _warten_bis_ende("mat", tmp_path / "p")
    assert s["ende"] and not s["laeuft"] and s.get("fehler") is None, s
    assert (s["done"], s["failed"], s["status"]) == (3, 0, "fertig")
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3
    assert "sk-geheim" not in json.dumps(json.loads((tmp_path / "p" / "mat.json").read_text()))  # kein Schlüssel im Befehl
    prozess.vergessen("mat", tmp_path / "p")
    assert prozess.stand("mat", tmp_path / "p") is None


def test_anhalten_beendet_den_prozess(tmp_path, seeds_datei):  # noqa: F811
    seeds, _ = seeds_datei
    with socket.socket() as s:  # freier Port, auf dem niemand antwortet: Verbindungsfehler, Lauf wartet und wiederholt
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg = LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", api_key="test", model="m", workers=2)
    _starten("mat", ["materialien", "--seeds", str(seeds), "--out", str(tmp_path / "materialien_t.jsonl")], cfg,
             tmp_path / "p")
    ende = time.time() + 30
    while not (prozess.stand("mat", tmp_path / "p") or {}).get("total") and time.time() < ende:
        time.sleep(0.2)
    assert prozess.laeuft("mat", tmp_path / "p")
    with pytest.raises(RuntimeError, match="läuft schon"):  # derselbe Name ein zweites Mal, solange er läuft
        _starten("mat", ["laeufe"], cfg, tmp_path / "p")
    prozess.anhalten("mat", tmp_path / "p")
    s = _warten_bis_ende("mat", tmp_path / "p", 60)
    assert not s["laeuft"] and s["ende"] and s["status"].startswith("pausiert"), s


def test_stand_schreiber_drosselt_und_meldet_ende(tmp_path):
    p = tmp_path / "x.stand.json"
    w = prozess.StandSchreiber(p, intervall=60)
    prog = Progress(total=5)
    w(prog)
    prog.done = 3
    w(prog)  # innerhalb des Intervalls: nicht geschrieben
    assert json.loads(p.read_text())["done"] == 0
    w.ende(prog)
    assert json.loads(p.read_text())["done"] == 3 and json.loads(p.read_text())["ende"]
    stop = prozess.stop_beobachten(p, intervall=0.05)
    assert not stop.wait(0.2)
    (tmp_path / "x.stop").touch()
    assert stop.wait(2)


def test_llm_umgebung_gibt_nur_den_eingetragenen_schluessel_weiter():
    args, env = prozess.llm_umgebung(LLMConfig(base_url="https://fremd.example/v1", api_key=None, model="m",
                                               reasoning_effort=None, service_tier="flex", workers=7))
    assert env["OPENAI_API_KEY"] == "" and env["OPENAI_BASE_URL"] == "https://fremd.example/v1"
    assert env["LLM_REASONING_EFFORT"] == "standard" and env["LLM_WORKERS"] == "7" and "--flex" in args
