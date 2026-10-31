"""LLM-Anbindung (OpenAI-kompatibel oder Anthropic) mit parallelen Workern, Wiederholung und Fortsetzung.

Jede Aufgabe hat eine eindeutige id. Ergebnisse werden zeilenweise an eine JSONL-Datei
angehängt; ein erneuter Lauf überspringt alle ids, die dort schon stehen. Fällt die API aus,
wird mit wachsender Wartezeit wiederholt. Bleibt sie länger weg, endet der Lauf geordnet
und kann später mit demselben Befehl fortgesetzt werden.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

ANBIETER = ("openai", "anthropic")  # openai = jede OpenAI-kompatible API (OpenAI, OpenRouter, …)
DEFAULT_MODELLE = {"openai": "gpt-6-luna", "anthropic": "claude-haiku-5-5"}
DEFAULT_MODEL = DEFAULT_MODELLE["openai"]
DEFAULT_BASE_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
# Anthropic verlangt eine Obergrenze für die Antwort (inklusive Denken); ein Volltext braucht weit weniger
ANTHROPIC_MAX_TOKENS = 16000
# OpenAI-Stufen ohne Gegenstück bei Anthropic (dort: low, medium, high, xhigh, max)
ANTHROPIC_EFFORT = {"none": "low", "minimal": "low"}
ANTHROPIC_JSON_HINWEIS = "Antworte ausschließlich mit einem JSON-Objekt, ohne Erklärung und ohne Codeblock."
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
    anbieter: str = "openai"        # "openai" (OpenAI-kompatibel) oder "anthropic"
    base_url: str | None = None
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    workers: int = DEFAULT_WORKERS
    max_retries: int = 6            # Versuche pro Aufgabe bei Fehlern (Rate-Limit, ungültiges JSON, Timeout)
    outage_minutes: float = 10.0    # so lange wird bei nicht erreichbarer API gewartet, dann pausiert der Lauf
    timeout: float = 180.0
    temperature: float | None = None  # None = Standard des Modells (manche Modelle akzeptieren keinen Wert)
    reasoning_effort: str | None = DEFAULT_REASONING_EFFORT  # Reasoning-Modelle; None = Modellstandard
    service_tier: str | None = None  # "flex" = halber Preis, langsamer (nur OpenAI); None = Standard
    max_tokens: int = ANTHROPIC_MAX_TOKENS  # nur Anthropic
    extra: dict = field(default_factory=dict)  # weitere Parameter für chat.completions.create bzw. messages.create

    @classmethod
    def from_env(cls, **overrides) -> "LLMConfig":
        """Werte aus der Umgebung: LLM_ANBIETER (openai oder anthropic), dazu OPENAI_BASE_URL/OPENAI_API_KEY bzw.
        ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY, LLM_MODEL, LLM_WORKERS, LLM_REASONING_EFFORT."""
        anbieter = overrides.get("anbieter") or os.environ.get("LLM_ANBIETER") or "openai"
        if anbieter not in ANBIETER:
            raise ValueError(f"Unbekannter Anbieter {anbieter!r}, möglich: {', '.join(ANBIETER)}")
        env = env_namen(anbieter)
        cfg = cls(
            anbieter=anbieter,
            base_url=os.environ.get(env["base_url"]) or None,
            api_key=os.environ.get(env["api_key"]) or None,
            model=os.environ.get("LLM_MODEL") or DEFAULT_MODELLE[anbieter],
            workers=int(os.environ.get("LLM_WORKERS") or DEFAULT_WORKERS),
            reasoning_effort=_effort(os.environ.get("LLM_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, _effort(v) if k == "reasoning_effort" else v)
        return cfg

    def public(self) -> dict:
        """Konfiguration ohne Schlüssel, für Herkunftsangaben."""
        out = {"base_url": self.base_url or DEFAULT_BASE_URLS[self.anbieter], "model": self.model}
        if self.anbieter != "openai":
            out["anbieter"] = self.anbieter
        if self.reasoning_effort:
            out["reasoning_effort"] = self.reasoning_effort
        if self.service_tier:
            out["service_tier"] = self.service_tier
        return out

    def pruefen(self):
        """Fehler in der Konfiguration, bevor eine Anfrage gestellt wird."""
        if self.anbieter not in ANBIETER:
            raise ValueError(f"Unbekannter Anbieter {self.anbieter!r}, möglich: {', '.join(ANBIETER)}")
        if self.anbieter == "anthropic" and self.service_tier:
            raise ValueError("Flex gibt es bei Anthropic nicht. Für den halben Preis Batch verwenden.")

    def parameter(self, system: str, user: str, json_mode: bool = False, batch: bool = False) -> dict:
        """Parameter für chat.completions.create bzw. (Anthropic) messages.create; batch=True für den Body einer
        Batch-Anfrage (ohne service_tier, Batch ist schon die günstige Verarbeitung)."""
        if self.anbieter == "anthropic":
            return self._anthropic_parameter(system, user, json_mode)
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

    def _anthropic_parameter(self, system: str, user: str, json_mode: bool) -> dict:
        """Systemprompt als Cache-Block: Er ist für alle Materialien eines Felds gleich, ab der Mindestlänge des
        Modells kosten Wiederholungen nur ein Zehntel (kürzere werden ohne Aufpreis nicht gecacht). temperature
        wird nicht gesendet: Aktuelle Claude-Modelle lehnen Sampling-Parameter ab."""
        if json_mode:  # Anthropic hat keinen JSON-Modus ohne Schema; json_aus_text liest auch ```json-Blöcke
            system = f"{system}\n\n{ANTHROPIC_JSON_HINWEIS}"
        kwargs = {"model": self.model, "max_tokens": self.max_tokens,
                  "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  "messages": [{"role": "user", "content": user}]}
        if self.reasoning_effort:
            kwargs["output_config"] = {"effort": ANTHROPIC_EFFORT.get(self.reasoning_effort, self.reasoning_effort)}
        return {**kwargs, **self.extra}


def env_namen(anbieter: str) -> dict:
    """Namen der Umgebungsvariablen für Base-URL und Key eines Anbieters."""
    p = "ANTHROPIC" if anbieter == "anthropic" else "OPENAI"
    return {"base_url": f"{p}_BASE_URL", "api_key": f"{p}_API_KEY"}


def api_key_fuer(base_url: str | None, api_key: str | None, env: LLMConfig) -> str | None:
    """Eingetragener Key, sonst der aus OPENAI_API_KEY (bzw. ANTHROPIC_API_KEY), aber nur für die Base-URL aus der Umgebung:
    Eine in der Oberfläche eingetragene andere Base-URL erhält den Key aus der Umgebung nicht."""
    if api_key:
        return api_key
    return env.api_key if (base_url or None) == env.base_url else None


def _effort(value: str | None) -> str | None:
    """„standard“ oder leer = Modellstandard (Parameter wird nicht gesendet)."""
    return None if not value or value == "standard" else value


class APIUnavailable(Exception):
    """Die API war länger als outage_minutes nicht erreichbar."""


class Abgebrochen(Exception):
    """Der Lauf soll anhalten: keine neue Anfrage mehr stellen."""


def _warten(stop: threading.Event | None, sekunden: float) -> bool:
    """Wartet in kleinen Schritten; True, sobald angehalten wurde (dann ohne die Wartezeit auszuschöpfen)."""
    for _ in range(int(sekunden / 0.5) + 1):
        if stop is not None and stop.is_set():
            return True
        time.sleep(min(0.5, sekunden))
    return stop is not None and stop.is_set()


TOKEN_FELDER = ("aufrufe", "eingabe", "davon_cache", "ausgabe", "davon_reasoning")


def tokens_aus_usage(u) -> dict:
    """Tokenzahlen einer Antwort; u ist das usage-Objekt der API oder (Batch-Ergebnis) ein dict."""
    def wert(obj, name):
        return (obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)) if obj is not None else None

    return {"aufrufe": 1, "eingabe": wert(u, "prompt_tokens") or 0, "ausgabe": wert(u, "completion_tokens") or 0,
            "davon_reasoning": wert(wert(u, "completion_tokens_details"), "reasoning_tokens") or 0,
            "davon_cache": wert(wert(u, "prompt_tokens_details"), "cached_tokens") or 0}


def anthropic_usage(u) -> dict:
    """usage einer Anthropic-Antwort in der Form von OpenAI (für tokens_aus_usage und Batch-Ergebnisse). Eingabe =
    alle Eingabe-Tokens, auch die aus dem Cache gelesenen und die in den Cache geschriebenen."""
    def wert(obj, name):
        return (obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)) if obj is not None else None

    lesen = wert(u, "cache_read_input_tokens") or 0
    eingabe = (wert(u, "input_tokens") or 0) + lesen + (wert(u, "cache_creation_input_tokens") or 0)
    return {"prompt_tokens": eingabe, "completion_tokens": wert(u, "output_tokens") or 0,
            "prompt_tokens_details": {"cached_tokens": lesen},
            "completion_tokens_details": {"reasoning_tokens": wert(wert(u, "output_tokens_details"),
                                                                   "thinking_tokens") or 0}}


def anthropic_text(msg) -> str:
    """Text einer Anthropic-Antwort (Denk-Blöcke weggelassen). Lehnt das Modell ab, ValueError (wird wiederholt)."""
    def wert(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    if wert(msg, "stop_reason") == "refusal":
        raise ValueError("Anthropic: Anfrage vom Modell abgelehnt (refusal)")
    return "".join(wert(b, "text") or "" for b in wert(msg, "content") or [] if wert(b, "type") == "text")


def json_aus_text(text: str):
    """JSON aus einer Modellantwort, auch wenn sie in ```json … ``` steht."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")  # Satz vor oder nach dem Objekt
        if 0 <= a < b:
            return json.loads(text[a:b + 1])
        raise


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        cfg.pruefen()
        if not cfg.api_key:
            raise ValueError(f"Kein API-Key: {env_namen(cfg.anbieter)['api_key']} setzen oder in der Oberfläche "
                             "eintragen.")
        self.cfg = cfg
        self.usage = threading.local()  # Tokenzähler je Thread (eine Aufgabe läuft in einem Thread)
        self.stop: threading.Event | None = None  # gesetzt: keine neue Anfrage mehr (Lauf hält an)
        self.nach_anfrage: Callable[[], None] | None = None  # nach jeder beantworteten Anfrage (Fortschritt)
        timeout = max(cfg.timeout, FLEX_TIMEOUT) if cfg.service_tier == "flex" else cfg.timeout
        if cfg.anbieter == "anthropic":
            from anthropic import Anthropic

            self.client = Anthropic(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout, max_retries=0)
        else:
            from openai import OpenAI

            self.client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout, max_retries=0)

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if self.stop is not None and self.stop.is_set():
            raise Abgebrochen()
        params = self.cfg.parameter(system, user, json_mode)
        if self.cfg.anbieter == "anthropic":
            msg = self.client.messages.create(**params)
            self._zaehlen(anthropic_usage(getattr(msg, "usage", None)))
            return anthropic_text(msg)
        if params.get("service_tier") == "flex":
            resp = self._flex(params)
        else:
            resp = self.client.chat.completions.create(**params)
        u = getattr(resp, "usage", None)
        self._zaehlen(u)
        return resp.choices[0].message.content or ""

    def _zaehlen(self, u):
        """Tokens einer beantworteten Anfrage zählen und den Fortschritt melden."""
        if u is not None:
            z = self.zaehler()
            for k, v in tokens_aus_usage(u).items():
                z[k] += v
        if self.nach_anfrage:
            self.nach_anfrage()

    def _flex(self, params: dict):
        """Flex-Anfrage; ohne Flex-Kapazität nach FLEX_VERSUCHE zum Standardpreis (gezählt als aufrufe_standard)."""
        import openai

        for i in range(FLEX_VERSUCHE):
            try:
                return self.client.chat.completions.create(**params)
            except openai.RateLimitError as e:
                if "flex" not in str(e).lower():
                    raise  # gewöhnliches Rate-Limit: run_tasks wiederholt
                if _warten(self.stop, FLEX_WARTEN * 2 ** i):
                    raise Abgebrochen() from e
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


def _sdks():
    """Die Fehlerklassen beider SDKs (anthropic nur, wenn installiert)."""
    import openai

    try:
        import anthropic
    except ImportError:
        return (openai,)
    return openai, anthropic


def _ist(e: Exception, *namen: str) -> bool:
    return any(isinstance(e, getattr(m, n)) for m in _sdks() for n in namen)


def _is_connection_error(e: Exception) -> bool:
    if _ist(e, "APIConnectionError", "APITimeoutError"):
        return True
    return _ist(e, "APIStatusError") and e.status_code >= 500  # bei Anthropic auch 529 (überlastet)


def _is_fatal(e: Exception) -> bool:
    # Guthaben aufgebraucht: Wiederholen hilft nicht, sonst scheitert der Rest (Anthropic meldet das mit 400)
    if _ist(e, "RateLimitError", "BadRequestError"):
        return any(k in str(e) for k in ("insufficient_quota", "credit_balance_exhausted", "credit balance is too low"))
    return _ist(e, "AuthenticationError", "PermissionDeniedError", "NotFoundError")


def _is_bad_request(e: Exception) -> bool:
    return _ist(e, "BadRequestError")


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


_ID_ZEILE = re.compile(rb'\{"id": "([^"\\]*)"')
_done_cache: dict[str, tuple[tuple[int, int], frozenset[str]]] = {}


def read_done(path: Path) -> set[str]:
    """IDs aller vollständigen Zeilen. Unsere Zeilen beginnen mit {"id": "…"; die ID wird ohne JSON-Parsen gelesen
    (sonst dauert eine 600-MB-Ausgabe ~10 s), Ergebnis je Datei bis zur nächsten Änderung gemerkt."""
    path = Path(path)
    try:
        s = path.stat()
    except FileNotFoundError:
        return set()
    key, stempel = str(path.resolve()), (s.st_mtime_ns, s.st_size)
    if (c := _done_cache.get(key)) and c[0] == stempel:
        return set(c[1])
    done = set()
    with path.open("rb") as f:
        for line in f:
            line = line.rstrip()
            if not line:
                continue
            m = _ID_ZEILE.match(line)
            if m and line.endswith(b"}"):
                done.add(m.group(1).decode("utf-8"))
                continue
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
                pass  # halbe Zeile nach Absturz
    _done_cache[key] = (stempel, frozenset(done))
    return done


@dataclass
class Progress:
    total: int
    done: int = 0
    failed: int = 0
    skipped: int = 0
    retries: int = 0                # wiederholte Versuche (Aufgabe neu gestartet), auch wenn sie danach klappten
    aktiv: int = 0                  # Aufgaben, an denen gerade ein Worker arbeitet
    anfragen: int = 0               # beantwortete API-Anfragen (zeigt auch zwischen fertigen Aufgaben, dass es läuft)
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
        if self.aktiv or self.anfragen:
            line += f" · {self.aktiv} in Arbeit, {self.anfragen} Anfragen beantwortet"
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

    def nach_anfrage():
        with write_lock:
            prog.anfragen += 1
        if on_progress:
            on_progress(prog)

    client.nach_anfrage = nach_anfrage
    outage = {"since": None}
    bad_requests = {"in_folge": 0}
    stop = stop_event or threading.Event()
    client.stop = stop  # nach dem Anhalten stellt kein Worker mehr eine neue Anfrage

    def attempt(task):
        if stop.is_set():
            return None, "abgebrochen"
        with write_lock:
            prog.aktiv += 1
        try:
            return _attempt(task)
        finally:
            with write_lock:
                prog.aktiv -= 1

    def _attempt(task):
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
            except Abgebrochen:
                return None, "abgebrochen"
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
                if _warten(stop, min(60, 2 ** i) + random.random()):
                    return None, "abgebrochen"
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
                finished, _ = wait(running, timeout=2, return_when=FIRST_COMPLETED)
                if stop.is_set() and prog.status == "läuft":
                    prog.status = "wird angehalten – laufende Anfragen werden noch beendet"
                    if on_progress:
                        on_progress(prog)
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
