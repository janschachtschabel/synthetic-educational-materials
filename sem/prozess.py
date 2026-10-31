"""Läufe als eigener Prozess: Die Oberfläche startet python -m sem … im Hintergrund und liest nur dessen Stand.

Im Streamlit-Prozess teilen sich Hunderte Worker-Threads den Interpreter mit der Oberfläche: Die Anzeige friert ein,
Klicks reagieren spät, und ein Neustart der App beendet den Lauf. Als eigener Prozess läuft er so schnell wie auf der
Kommandozeile und weiter, auch wenn die Seite geschlossen oder die App neu gestartet wird.

Austausch über Dateien in output/.prozesse/:
  <name>.json        Start: Befehl ohne Schlüssel, PID, Startzeit
  <name>.stand.json  Fortschritt, vom Lauf höchstens einmal je Sekunde geschrieben, am Ende mit "ende": true
  <name>.stop        von der Oberfläche angelegt: Der Lauf startet keine neue Anfrage mehr und hält an
  <name>.log         Ausgabe des Prozesses (Fehlermeldungen)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .llm import LLMConfig, Progress, _prozess_laeuft
from .paths import OUTPUT_DIR, ROOT

PROZESS_DIR = OUTPUT_DIR / ".prozesse"
_KINDER: dict[str, subprocess.Popen] = {}  # in diesem Prozess gestartete Läufe (unter Linux sonst Zombies)


def _pfad(name: str, endung: str, ordner: Path | None = None) -> Path:
    return (ordner or PROZESS_DIR) / f"{name}{endung}"


def _json_schreiben(p: Path, daten: dict):
    """Erst in eine Nebendatei, dann umbenennen: Die Oberfläche liest nie eine halb geschriebene Datei."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _json_lesen(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --- Oberfläche --------------------------------------------------------------------------------------------------
def llm_umgebung(cfg: LLMConfig) -> tuple[list[str], dict]:
    """Argumente und Umgebung, mit denen der Lauf genau diese Konfiguration bekommt. Der Schlüssel geht nur über die
    Umgebung (nicht in der Prozessliste sichtbar), und nur der eingetragene: Ein Schlüssel aus OPENAI_API_KEY darf
    nicht an eine andere Base-URL gehen."""
    env = {"OPENAI_API_KEY": cfg.api_key or "", "OPENAI_BASE_URL": cfg.base_url or "", "LLM_MODEL": cfg.model,
           "LLM_WORKERS": str(cfg.workers), "LLM_REASONING_EFFORT": cfg.reasoning_effort or "standard"}
    args = ["--retries", str(cfg.max_retries), "--ausfall-minuten", str(cfg.outage_minutes)]
    if cfg.service_tier == "flex":
        args.append("--flex")
    return args, env


def starten(name: str, argv: list[str], env: dict | None = None, ordner: Path | None = None) -> None:
    """python -m sem <argv> im Hintergrund starten; der Lauf schreibt seinen Stand nach <name>.stand.json."""
    if laeuft(name, ordner):
        raise RuntimeError(f"Lauf {name} läuft schon")
    d = ordner or PROZESS_DIR
    d.mkdir(parents=True, exist_ok=True)
    for endung in (".stand.json", ".stop"):
        _pfad(name, endung, d).unlink(missing_ok=True)
    befehl = [sys.executable, "-m", "sem", *argv, "--stand", str(_pfad(name, ".stand.json", d))]
    optionen: dict = {}
    if os.name == "nt":  # ohne Konsolenfenster und unabhängig von Strg+C im Fenster der App
        optionen["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        optionen["start_new_session"] = True
    with open(_pfad(name, ".log", d), "w", encoding="utf-8") as log:
        proc = subprocess.Popen(befehl, cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **optionen)
    _KINDER[str(_pfad(name, "", d))] = proc
    _json_schreiben(_pfad(name, ".json", d), {"pid": proc.pid, "befehl": ["python", "-m", "sem", *argv],
                                              "gestartet": datetime.now().isoformat(timespec="seconds")})


def laeuft(name: str, ordner: Path | None = None) -> bool:
    proc = _KINDER.get(str(_pfad(name, "", ordner)))
    if proc is not None:
        return proc.poll() is None
    info = _json_lesen(_pfad(name, ".json", ordner))
    return bool(info) and _prozess_laeuft(info["pid"])


def stand(name: str, ordner: Path | None = None) -> dict | None:
    """Letzter Stand eines Laufs (None: nie gestartet). laeuft: Prozess lebt; endete er ohne Abschlussmeldung,
    steht in fehler die letzte Zeile seiner Ausgabe."""
    info = _json_lesen(_pfad(name, ".json", ordner))
    if not info:
        return None
    s = _json_lesen(_pfad(name, ".stand.json", ordner)) or {}
    s = {**info, **s, "laeuft": laeuft(name, ordner), "angehalten": _pfad(name, ".stop", ordner).exists()}
    if not s["laeuft"] and not s.get("ende"):
        log = _pfad(name, ".log", ordner)
        zeilen = [z for z in log.read_text(encoding="utf-8", errors="replace").splitlines() if z.strip()] \
            if log.exists() else []
        s["fehler"] = s.get("fehler") or (zeilen[-1] if zeilen else "Lauf beendet ohne Meldung")
    return s


def anhalten(name: str, ordner: Path | None = None):
    _pfad(name, ".stop", ordner).touch()


def vergessen(name: str, ordner: Path | None = None):
    """Anzeige eines beendeten Laufs entfernen (die Ausgabe des Laufs bleibt)."""
    if laeuft(name, ordner):
        return
    for endung in (".json", ".stand.json", ".stop", ".log"):
        _pfad(name, endung, ordner).unlink(missing_ok=True)
    _KINDER.pop(str(_pfad(name, "", ordner)), None)


# --- Lauf (Kommandozeile mit --stand) ----------------------------------------------------------------------------
class StandSchreiber:
    """on_progress für run_tasks: schreibt den Stand höchstens alle intervall Sekunden in eine kleine Datei."""

    def __init__(self, pfad: Path, intervall: float = 1.0):
        self.pfad, self.intervall, self.zuletzt = Path(pfad), intervall, 0.0
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self._sperre = threading.Lock()

    def __call__(self, p: Progress, ende: bool = False, fehler: str | None = None):
        jetzt = time.time()
        if not ende and jetzt - self.zuletzt < self.intervall:
            return
        with self._sperre:
            self.zuletzt = jetzt
            _json_schreiben(self.pfad, {
                "total": p.total, "done": p.done, "failed": p.failed, "skipped": p.skipped, "retries": p.retries,
                "aktiv": p.aktiv, "anfragen": p.anfragen, "status": p.status, "zeile": p.line(),
                "aktualisiert": datetime.now().isoformat(timespec="seconds"), "ende": ende, "fehler": fehler})

    def ende(self, p: Progress | None, fehler: str | None = None):
        self(p or Progress(total=0, status="abgebrochen"), ende=True, fehler=fehler)


def stop_beobachten(stand_pfad: Path, intervall: float = 0.5) -> threading.Event:
    """Event, das gesetzt wird, sobald die Oberfläche <name>.stop anlegt."""
    stop = threading.Event()
    stop_datei = Path(stand_pfad).with_name(Path(stand_pfad).name.removesuffix(".stand.json") + ".stop")

    def beobachten():
        while not stop.is_set():
            if stop_datei.exists():
                stop.set()
            time.sleep(intervall)

    threading.Thread(target=beobachten, daemon=True).start()
    return stop
