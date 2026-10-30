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
# Ein HTTP 400 betrifft meist eine einzelne Aufgabe (z. B. Kontext zu lang): protokollieren und weitermachen.
# Scheitern so viele Aufgaben in Folge damit, liegt es an der Konfiguration: Lauf pausieren.
MAX_BAD_REQUESTS_IN_FOLGE = 5
# Flex (OpenAI): halber Preis, aber langsamere Antworten und bei Engpässen Ablehnung (429, wird wiederholt);
# deshalb längere Wartezeit je Anfrage
FLEX_TIMEOUT = 900.0
# Hat Flex keine Kapazität (429), wird die Anfrage so oft mit Flex wiederholt (Wartezeit verdoppelt sich) und dann
# zum Standardpreis gestellt; gemessen am 30.10.2026 lehnte Flex sonst die Hälfte der Materialien ab
FLEX_VERSUCHE = 3
FLEX_WARTEN = 10.0


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
    service_tier: str | None = None  # "flex" = halber Preis, langsamer (OpenAI); None = Standard
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
        if self.service_tier:
            out["service_tier"] = self.service_tier
        return out

    def parameter(self, system: str, user: str, json_mode: bool = False, batch: bool = False) -> dict:
        """Parameter für chat.completions.create; batch=True für den Body einer Batch-Anfrage (ohne service_tier,
        Batch ist schon die günstige Verarbeitung)."""
        kwargs = dict(self.extra)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.service_tier and not batch:
            kwargs["service_tier"] = self.service_tier
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], **kwargs}


def api_key_fuer(base_url: str | None, api_key: str | None, env: LLMConfig) -> str | None:
    """Eingetragener Key, sonst der aus OPENAI_API_KEY, diesen aber nur für die Base-URL aus der Umgebung:
    Eine in der Oberfläche eingetragene andere Base-URL erhält den Key aus der Umgebung nicht."""
    if api_key:
        return api_key
    return env.api_key if (base_url or None) == env.base_url else None


def _effort(value: str | None) -> str | None:
    """„standard“ oder leer = Modellstandard (Parameter wird nicht gesendet)."""
    return None if not value or value == "standard" else value


class APIUnavailable(Exception):
    """Die API war länger als outage_minutes nicht erreichbar."""


TOKEN_FELDER = ("aufrufe", "eingabe", "davon_cache", "ausgabe", "davon_reasoning")


def tokens_aus_usage(u) -> dict:
    """Tokenzahlen einer Antwort; u ist das usage-Objekt der API oder (Batch-Ergebnis) ein dict."""
    def wert(obj, name):
        return (obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)) if obj is not None else None

    return {"aufrufe": 1, "eingabe": wert(u, "prompt_tokens") or 0, "ausgabe": wert(u, "completion_tokens") or 0,
            "davon_reasoning": wert(wert(u, "completion_tokens_details"), "reasoning_tokens") or 0,
            "davon_cache": wert(wert(u, "prompt_tokens_details"), "cached_tokens") or 0}


def json_aus_text(text: str):
    """JSON aus einer Modellantwort, auch wenn sie in ```json … ``` steht."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        from openai import OpenAI

        if not cfg.api_key:
            raise ValueError("Kein API-Key: OPENAI_API_KEY setzen oder in der Oberfläche eintragen.")
        self.cfg = cfg
        self.usage = threading.local()  # Tokenzähler je Thread (eine Aufgabe läuft in einem Thread)
        timeout = max(cfg.timeout, FLEX_TIMEOUT) if cfg.service_tier == "flex" else cfg.timeout
        self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout, max_retries=0)

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        params = self.cfg.parameter(system, user, json_mode)
        resp = self._flex(params) if params.get("service_tier") == "flex" else             self.client.chat.completions.create(**params)
        u = getattr(resp, "usage", None)
        if u is not None:
            z = self.zaehler()
            for k, v in tokens_aus_usage(u).items():
                z[k] += v
        return resp.choices[0].message.content or ""

    def _flex(self, params: dict):
        """Flex-Anfrage; ohne Flex-Kapazität nach FLEX_VERSUCHE zum Standardpreis (gezählt als aufrufe_standard)."""
        import openai

        for i in range(FLEX_VERSUCHE):
            try:
                return self.client.chat.completions.create(**params)
            except openai.RateLimitError as e:
                if "flex" not in str(e).lower():
                    raise  # gewöhnliches Rate-Limit: run_tasks wiederholt
                time.sleep(FLEX_WARTEN * 2 ** i)
        z = self.zaehler()
        z["aufrufe_standard"] = z.get("aufrufe_standard", 0) + 1
        return self.client.chat.completions.create(**{k: v for k, v in params.items() if k != "service_tier"})

    def zaehler(self, neu: bool = False) -> dict:
        """Tokenzähler des aktuellen Threads; neu=True setzt ihn zurück."""
        if neu or not hasattr(self.usage, "z"):
            self.usage.z = dict.fromkeys(TOKEN_FELDER, 0)
        return self.usage.z

    def chat_json(self, system: str, user: str) -> dict:
        return json_aus_text(self.chat(system, user, json_mode=True))


def _is_connection_error(e: Exception) -> bool:
    import openai

    if isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
        return True
    return isinstance(e, openai.APIStatusError) and e.status_code >= 500


def _is_fatal(e: Exception) -> bool:
    import openai

    return isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError))


def _is_bad_request(e: Exception) -> bool:
    import openai

    return isinstance(e, openai.BadRequestError)


def _prozess_laeuft(pid: int) -> bool:
    """Ob ein Prozess dieser Nummer auf diesem Rechner läuft."""
    if os.name == "nt":
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k.GetLastError() == 5  # Zugriff verweigert: der Prozess existiert
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sperre_nehmen(lock_path: Path):
    """Legt die Sperrdatei eines Laufs an (Prozessnummer und Rechner). Gehört eine vorhandene Sperre zu einem
    Prozess, der auf diesem Rechner nicht mehr läuft (Absturz, Abbruch), wird sie übernommen; sonst RuntimeError."""
    import socket

    rechner = socket.gethostname()
    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {rechner}".encode())
            os.close(fd)
            return
        except FileExistsError:
            teile = Path(lock_path).read_text(encoding="utf-8", errors="replace").split()
            if len(teile) == 2 and teile[0].isdigit() and teile[1] == rechner and not _prozess_laeuft(int(teile[0])):
                Path(lock_path).unlink(missing_ok=True)  # verwaist
                continue
            break
    raise RuntimeError(f"{Path(lock_path).name} existiert: Es läuft schon ein Lauf auf dieser Datei. Falls nicht "
                       "(z. B. nach Absturz auf einem anderen Rechner), die Datei löschen und neu starten.")


def _zeilenende(path: Path):
    """Endet die Datei nach einem Absturz mitten in einer Zeile, wird die halbe Zeile abgeschlossen, damit der
    nächste Datensatz in einer eigenen Zeile steht (read_done überspringt die halbe)."""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as f:
            f.seek(-1, 2)
            fehlt = f.read(1) != b"\n"
        if fehlt:
            with path.open("ab") as f:
                f.write(b"\n")


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

    sperre_nehmen(lock_path)

    client = LLMClient(cfg)
    write_lock = threading.Lock()
    outage = {"since": None}
    bad_requests = {"in_folge": 0}
    stop = stop_event or threading.Event()

    def attempt(task):
        last = None
        for i in range(cfg.max_retries):
            if stop.is_set():
                return None, "abgebrochen"
            try:
                result = worker(client, task)
                outage["since"] = None
                with write_lock:
                    bad_requests["in_folge"] = 0
                return result, None
            except Exception as e:  # noqa: BLE001 - alles außer fatalen Fehlern wird wiederholt
                if _is_fatal(e):
                    stop.set()
                    return None, f"{type(e).__name__}: {e}"
                if _is_bad_request(e):  # Wiederholen hilft nicht
                    with write_lock:
                        bad_requests["in_folge"] += 1
                        if bad_requests["in_folge"] >= MAX_BAD_REQUESTS_IN_FOLGE:
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
        _zeilenende(out_path)
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
