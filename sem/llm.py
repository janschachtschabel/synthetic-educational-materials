"""OpenAI-kompatible LLM-Anbindung mit parallelen Workern, Wiederholung und Fortsetzung.

Jede Aufgabe hat eine eindeutige id. Ergebnisse werden zeilenweise an eine JSONL-Datei
angehängt; ein erneuter Lauf überspringt alle ids, die dort schon stehen. Fällt die API aus,
wird mit wachsender Wartezeit wiederholt. Bleibt sie länger weg, endet der Lauf geordnet
und kann später mit demselben Befehl fortgesetzt werden.
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_WORKERS = 20
DEFAULT_REASONING_EFFORT = "low"  # gemessen: ein Drittel schneller als der Modellstandard, gleiche Prüfwerte


@dataclass
class LLMConfig:
    base_url: str | None = None
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    workers: int = DEFAULT_WORKERS
    max_retries: int = 6            # Versuche pro Aufgabe bei Fehlern (Rate-Limit, ungültiges JSON, Timeout)
    outage_minutes: float = 10.0    # so lange wird bei nicht erreichbarer API gewartet, dann pausiert der Lauf
    timeout: float = 180.0
    temperature: float | None = None  # None = Standard des Modells (manche Modelle akzeptieren keinen Wert)
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT  # Reasoning-Modelle; None = Modellstandard
    extra: dict = field(default_factory=dict)  # weitere Parameter für chat.completions.create

    @classmethod
    def from_env(cls, **overrides) -> "LLMConfig":
        cfg = cls(
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            api_key=os.environ.get("OPENAI_API_KEY") or None,
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
            workers=int(os.environ.get("LLM_WORKERS") or DEFAULT_WORKERS),
            reasoning_effort=_effort(os.environ.get("LLM_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, _effort(v) if k == "reasoning_effort" else v)
        return cfg

    def public(self) -> dict:
        """Konfiguration ohne Schlüssel, für Herkunftsangaben."""
        out = {"base_url": self.base_url or "https://api.openai.com/v1", "model": self.model}
        if self.reasoning_effort:
            out["reasoning_effort"] = self.reasoning_effort
        return out


def _effort(value: str | None) -> str | None:
    """„standard“ oder leer = Modellstandard (Parameter wird nicht gesendet)."""
    return None if not value or value == "standard" else value


class APIUnavailable(Exception):
    """Die API war länger als outage_minutes nicht erreichbar."""


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        from openai import OpenAI

        if not cfg.api_key:
            raise ValueError("Kein API-Key: OPENAI_API_KEY setzen oder in der Oberfläche eintragen.")
        self.cfg = cfg
        self.usage = threading.local()  # Tokenzähler je Thread (eine Aufgabe läuft in einem Thread)
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout, max_retries=0)

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs = dict(self.cfg.extra)
        if self.cfg.temperature is not None:
            kwargs["temperature"] = self.cfg.temperature
        if self.cfg.reasoning_effort:
            kwargs["reasoning_effort"] = self.cfg.reasoning_effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            z = self.zaehler()
            z["aufrufe"] += 1
            z["eingabe"] += u.prompt_tokens or 0
            z["ausgabe"] += u.completion_tokens or 0
            det = getattr(u, "completion_tokens_details", None)
            z["davon_reasoning"] += (getattr(det, "reasoning_tokens", 0) or 0) if det else 0
            pdet = getattr(u, "prompt_tokens_details", None)
            z["davon_cache"] += (getattr(pdet, "cached_tokens", 0) or 0) if pdet else 0
        return resp.choices[0].message.content or ""

    def zaehler(self, neu: bool = False) -> dict:
        """Tokenzähler des aktuellen Threads; neu=True setzt ihn zurück."""
        if neu or not hasattr(self.usage, "z"):
            self.usage.z = {"aufrufe": 0, "eingabe": 0, "davon_cache": 0, "ausgabe": 0, "davon_reasoning": 0}
        return self.usage.z

    def chat_json(self, system: str, user: str) -> dict:
        text = self.chat(system, user, json_mode=True).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return json.loads(text)


def _is_connection_error(e: Exception) -> bool:
    import openai

    if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    return isinstance(e, openai.APIStatusError) and e.status_code >= 500


def _is_fatal(e: Exception) -> bool:
    import openai

    return isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError,
                          openai.BadRequestError))


def read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                pass  # halbe Zeile nach Absturz
    return done


@dataclass
class Progress:
    total: int
    done: int = 0
    failed: int = 0
    skipped: int = 0
    retries: int = 0                # wiederholte Versuche (Aufgabe neu gestartet), auch wenn sie danach klappten
    started: float = field(default_factory=time.time)
    status: str = "läuft"
    last_error: str | None = None

    @property
    def rate(self) -> float:
        dt = time.time() - self.started
        return self.done / dt if dt > 0 else 0.0

    @property
    def eta_seconds(self) -> float | None:
        left = self.total - self.done - self.failed
        return left / self.rate if self.rate > 0 else None

    def line(self) -> str:
        eta = self.eta_seconds
        eta_s = f"{int(eta // 60)}:{int(eta % 60):02d} min" if eta is not None else "–"
        line = (f"{self.done + self.failed} von {self.total} bearbeitet: {self.done} erfolgreich, "
                f"{self.failed} fehlgeschlagen, {self.retries} Wiederholungen · {self.rate * 60:.1f} pro Minute · "
                f"Rest {eta_s} · {self.status}")
        if self.last_error and self.status == "läuft":
            line += f"  letzte Meldung: {self.last_error[:120]}"
        return line


def print_progress(p: Progress):
    sys.stderr.write("\r" + p.line().ljust(90))
    sys.stderr.flush()


def run_tasks(
    tasks: Iterable[dict],
    worker: Callable[[LLMClient, dict], dict],
    out_path: Path,
    cfg: LLMConfig,
    on_progress: Callable[[Progress], None] | None = print_progress,
    stop_event: threading.Event | None = None,
) -> Progress:
    """Führt worker(client, task) für alle Aufgaben aus, die noch nicht in out_path stehen.

    worker gibt ein dict zurück, das mindestens "id" enthält. Fehlgeschlagene Aufgaben werden
    in <out_path>.fehler.jsonl protokolliert und beim nächsten Lauf erneut versucht.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path = out_path.with_suffix(".fehler.jsonl")
    lock_path = out_path.with_suffix(".lock")
    tasks = list(tasks)
    done_ids = read_done(out_path)
    todo = [t for t in tasks if t["id"] not in done_ids]
    prog = Progress(total=len(todo), skipped=len(tasks) - len(todo))
    if not todo:
        prog.status = "fertig"
        if on_progress:
            on_progress(prog)
        return prog

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise RuntimeError(f"{lock_path.name} existiert: Es läuft schon ein Lauf auf dieser Datei. "
                           "Falls nicht (z. B. nach Absturz), die Datei löschen und neu starten.")

    client = LLMClient(cfg)
    write_lock = threading.Lock()
    outage = {"since": None}
    stop = stop_event or threading.Event()

    def attempt(task):
        last = None
        for i in range(cfg.max_retries):
            if stop.is_set():
                return None, "abgebrochen"
            try:
                result = worker(client, task)
                outage["since"] = None
                return result, None
            except Exception as e:  # noqa: BLE001 - alles außer fatalen Fehlern wird wiederholt
                if _is_fatal(e):
                    stop.set()
                    return None, f"{type(e).__name__}: {e}"
                last = f"{type(e).__name__}: {e}"
                with write_lock:
                    prog.retries += 1
                    prog.last_error = last
                if _is_connection_error(e):
                    outage["since"] = outage["since"] or time.time()
                    if time.time() - outage["since"] > cfg.outage_minutes * 60:
                        stop.set()
                        return None, "API nicht erreichbar"
                time.sleep(min(60, 2 ** i) + random.random())
        return None, last

    try:
        with out_path.open("a", encoding="utf-8") as fout, err_path.open("a", encoding="utf-8") as ferr, \
                ThreadPoolExecutor(cfg.workers) as pool:
            it = iter(todo)
            running = {}
            for task in it:
                running[pool.submit(attempt, task)] = task
                if len(running) >= cfg.workers * 2:
                    break
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    task = running.pop(fut)
                    result, error = fut.result()
                    with write_lock:
                        if result is not None:
                            fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                            fout.flush()
                            prog.done += 1
                        elif error != "abgebrochen":
                            prog.last_error = error
                            ferr.write(json.dumps({"id": task["id"], "fehler": error}, ensure_ascii=False) + "\n")
                            ferr.flush()
                            prog.failed += 1
                    if on_progress:
                        on_progress(prog)
                    if not stop.is_set():
                        nxt = next(it, None)
                        if nxt is not None:
                            running[pool.submit(attempt, nxt)] = nxt
    finally:
        lock_path.unlink(missing_ok=True)

    if stop.is_set():
        prog.status = "pausiert – mit demselben Befehl fortsetzen"
        if prog.last_error:
            prog.status += f" (letzter Fehler: {prog.last_error})"
    else:
        prog.status = "fertig"
    if on_progress:
        on_progress(prog)
        if on_progress is print_progress:
            sys.stderr.write("\n")
    return prog
