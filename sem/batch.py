"""Materialien über die Batch-Schnittstelle von OpenAI erzeugen: halber Preis, Ergebnis je Runde bis zu 24 h.

Jedes Material durchläuft dieselben Stufen wie bei der sofortigen Erzeugung (generate.Material: Volltext,
Titel, Beschreibung, Keywords, dann Inhaltstyp-Prüfung und Frage-Antwort-Paare). Eine Runde schickt die
anstehenden Anfragen aller Materialien gesammelt als Batch; ist sie fertig, rückt jedes Material eine Stufe
weiter. Unbrauchbare Antworten werden in der nächsten Runde neu angefragt, wie bei der sofortigen Erzeugung.

Der Stand liegt in <ausgabe>.batch/stand.json (dazu die hochgeladenen Anfragedateien). Jeder Aufruf von
BatchLauf.weiter() holt fertige Batches ab, wertet sie aus, schreibt fertige Materialien in die Ausgabedatei
und schickt die nächste Runde; er kann jederzeit wiederholt werden. Fertige Materialien sehen aus wie bei der
sofortigen Erzeugung, mit "verarbeitung": "batch" in synth:generierung.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import schema as S
from .bereiche import get as get_bereich
from .generate import Material, meta_fuer, read_seeds
from .laeufe import lauf_speichern
from .llm import LLMConfig, _zeilenende, read_done, sperre_nehmen, tokens_aus_usage
from .prompts import load_prompts

ENDPUNKT = "/v1/chat/completions"
MAX_ANFRAGEN = 50_000            # je Batch-Datei (Grenze der API)
MAX_BYTES = 190 * 1024 ** 2      # je Batch-Datei (Grenze der API: 200 MB)
MAX_API_FEHLER = 3               # so oft wird eine Anfrage neu gestellt, die im Batch selbst scheitert
FERTIG = {"completed", "failed", "expired", "cancelled"}


def batch_ordner(out_path: Path) -> Path:
    return Path(out_path).with_name(Path(out_path).stem + ".batch")


class BatchLauf:
    def __init__(self, seeds_path: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
                 inhaltstyp_pruefen: bool = True, ausgabesprache: str = "de", client=None):
        self.out_path = Path(out_path)
        self.err_path = self.out_path.with_suffix(".fehler.jsonl")
        self.ordner = batch_ordner(self.out_path)
        self.stand_path = self.ordner / "stand.json"
        self.cfg = cfg
        self.inhaltstyp_pruefen = inhaltstyp_pruefen
        self.ausgabesprache = ausgabesprache
        seeds = read_seeds(seeds_path)
        self.seeds = {s["id"]: s for s in (seeds[:limit] if limit else seeds)}
        keys = {s[S.F_BEREICH] for s in self.seeds.values()}
        self.bereiche = {k: get_bereich(k) for k in keys}
        self.prompts = {k: load_prompts(k) for k in keys}
        self.meta = meta_fuer(cfg, self.prompts, inhaltstyp_pruefen, ausgabesprache, verarbeitung="batch")
        if client is None:
            from openai import OpenAI

            if not cfg.api_key:
                raise ValueError("Kein API-Key: OPENAI_API_KEY setzen oder in der Oberfläche eintragen.")
            client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout)
        self.client = client
        parameter = {"modell": cfg.model, "prompt_hash": self.meta["prompt_hash"],
                     "inhaltstyp_pruefung": inhaltstyp_pruefen, "ausgabesprache": ausgabesprache,
                     "reasoning_effort": cfg.reasoning_effort}
        if self.stand_path.exists():
            self.stand = json.loads(self.stand_path.read_text(encoding="utf-8"))
            if self.stand["parameter"] != parameter:
                raise ValueError(f"{self.ordner.name} gehört zu einem Lauf mit anderen Einstellungen oder Prompts "
                                 f"({self.stand['parameter']}). Für einen neuen Lauf einen anderen Ausgabenamen wählen.")
        else:
            self.stand = {"gestartet": time.time(), "parameter": parameter, "runde": 0, "materialien": {},
                          "fehlgeschlagen": [], "batches": []}
        lauf_speichern(self.out_path, seeds_path, cfg, limit, inhaltstyp_pruefen, ausgabesprache, "batch")

    # --- Stand ------------------------------------------------------------------------------------

    def _speichern(self):
        self.ordner.mkdir(parents=True, exist_ok=True)
        tmp = self.stand_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.stand, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.stand_path)

    @contextmanager
    def _sperre(self):
        """Dieselbe Sperrdatei wie die sofortige Erzeugung: nie zwei Läufe auf einer Ausgabedatei."""
        lock = self.out_path.with_suffix(".lock")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        sperre_nehmen(lock)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _material(self, sid: str) -> Material:
        seed = self.seeds[sid]
        z = self.stand["materialien"].get(sid)
        return Material(seed, self.prompts, inhaltstyp_pruefen=self.inhaltstyp_pruefen,
                        ausgabesprache=self.ausgabesprache, zustand=z, bereich=self.bereiche[seed[S.F_BEREICH]])

    def _offene_ids(self) -> list[str]:
        erledigt = read_done(self.out_path) | set(self.stand["fehlgeschlagen"])
        return [sid for sid in self.seeds if sid not in erledigt]

    def _unterwegs(self) -> set[tuple[str, str]]:
        """(Material, Feld) der Anfragen in Batches, die noch nicht ausgewertet sind."""
        return {tuple(c.split("|")[:2]) for b in self.stand["batches"] if not b["ausgewertet"] for c in b["anfragen"]}

    # --- Ablauf -----------------------------------------------------------------------------------

    def weiter(self) -> dict:
        """Fertige Batches abholen und auswerten, dann die nächste Runde schicken. Gibt den Stand zurück."""
        with self._sperre():
            for b in self.stand["batches"]:
                if not b["ausgewertet"]:
                    self._abholen(b)
            self._abschicken()
            self._speichern()
        return self.status()

    def _abholen(self, b: dict):
        info = self.client.batches.retrieve(b["id"])
        b["status"] = info.status
        if info.status not in FERTIG:
            return
        beantwortet = set()
        for datei_id in (info.output_file_id, info.error_file_id):
            if not datei_id:
                continue
            for zeile in self.client.files.content(datei_id).text.splitlines():
                if zeile.strip():
                    r = json.loads(zeile)
                    beantwortet.add(r["custom_id"])
                    self._ergebnis(r)
        # abgelaufen, abgebrochen oder ohne Zeile: neu stellen
        for cid in b["anfragen"]:
            if cid not in beantwortet:
                self._api_fehler(cid, f"Batch {info.status}")
        b["ausgewertet"] = True
        self._speichern()

    def _ergebnis(self, r: dict):
        cid = r["custom_id"]
        sid, feld = cid.split("|")[:2]
        if sid not in self.seeds or sid in self.stand["fehlgeschlagen"]:
            return
        resp = r.get("response") or {}
        if resp.get("status_code") != 200:
            fehler = (resp.get("body") or {}).get("error") or r.get("error") or resp.get("status_code")
            self._api_fehler(cid, str(fehler)[:300])
            return
        body = resp["body"]
        text = body["choices"][0]["message"].get("content") or ""
        m = self._material(sid)
        try:
            m.antwort(feld, text, tokens_aus_usage(body.get("usage")))
        except ValueError as e:
            self._fehlgeschlagen(sid, str(e))
            return
        if m.fertig:
            dauer = time.time() - self.stand["gestartet"]
            _zeilenende(self.out_path)
            with self.out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(m.ergebnis(self.meta, dauer), ensure_ascii=False) + "\n")
            self.stand["materialien"].pop(sid, None)
        else:
            self.stand["materialien"][sid] = {**self.stand["materialien"].get(sid, {}), **m.zustand()}

    def _api_fehler(self, cid: str, grund: str):
        sid = cid.split("|")[0]
        if sid in self.stand["fehlgeschlagen"]:
            return
        z = self.stand["materialien"].setdefault(sid, {})
        z["api_fehler"] = z.get("api_fehler", 0) + 1
        if z["api_fehler"] >= MAX_API_FEHLER:
            self._fehlgeschlagen(sid, f"Batch-Anfrage {MAX_API_FEHLER}x gescheitert: {grund}")

    def _fehlgeschlagen(self, sid: str, grund: str):
        self.stand["materialien"].pop(sid, None)
        self.stand["fehlgeschlagen"].append(sid)
        _zeilenende(self.err_path)
        with self.err_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": sid, "fehler": grund}, ensure_ascii=False) + "\n")

    def _abschicken(self):
        unterwegs = self._unterwegs()
        zeilen = []
        runde = self.stand["runde"] + 1
        for sid in self._offene_ids():
            for a in self._material(sid).anfragen():
                if (sid, a.feld) not in unterwegs:
                    body = self.cfg.parameter(a.system, a.user, a.json_mode, batch=True)
                    zeilen.append((f"{sid}|{a.feld}|{runde}",
                                   json.dumps({"custom_id": f"{sid}|{a.feld}|{runde}", "method": "POST",
                                               "url": ENDPUNKT, "body": body}, ensure_ascii=False)))
        if not zeilen:
            return
        self.stand["runde"] = runde
        self.ordner.mkdir(parents=True, exist_ok=True)
        teil, groesse, nr = [], 0, 0
        for cid, zeile in zeilen + [(None, None)]:
            n = len(zeile.encode("utf-8")) + 1 if zeile else 0
            if teil and (zeile is None or len(teil) >= MAX_ANFRAGEN or groesse + n > MAX_BYTES):
                nr += 1
                self._batch_anlegen(runde, nr, teil)
                teil, groesse = [], 0
            if zeile is not None:
                teil.append((cid, zeile))
                groesse += n

    def _batch_anlegen(self, runde: int, nr: int, teil: list[tuple[str, str]]):
        datei = self.ordner / f"runde{runde:03d}_{nr:02d}.jsonl"
        datei.write_text("".join(z + "\n" for _, z in teil), encoding="utf-8")
        with datei.open("rb") as f:
            hochgeladen = self.client.files.create(file=f, purpose="batch")
        b = self.client.batches.create(input_file_id=hochgeladen.id, endpoint=ENDPUNKT, completion_window="24h",
                                       metadata={"zweck": "synthetic-educational-materials", "runde": str(runde)})
        self.stand["batches"].append({"id": b.id, "runde": runde, "datei": datei.name, "status": b.status,
                                      "gestartet": datetime.now().isoformat(timespec="seconds"),
                                      "anfragen": [c for c, _ in teil], "ausgewertet": False})
        self._speichern()  # sofort: nach einem Absturz wird nichts doppelt geschickt

    # --- Übersicht --------------------------------------------------------------------------------

    def status(self) -> dict:
        fertig = len(read_done(self.out_path) & set(self.seeds))
        stufen: dict[str, int] = {}
        for sid in self._offene_ids():
            felder = self.stand["materialien"].get(sid, {}).get("felder", {})
            stufe = next((f for f in ("volltext", "titel", "beschreibung", "keywords") if f not in felder),
                         "abschluss")
            stufen[stufe] = stufen.get(stufe, 0) + 1
        laufend = [b for b in self.stand["batches"] if not b["ausgewertet"]]
        offen = sum(stufen.values())
        return {"materialien": len(self.seeds), "fertig": fertig, "fehlgeschlagen": len(self.stand["fehlgeschlagen"]),
                "offen": offen, "offen_je_stufe": stufen, "runde": self.stand["runde"],
                "batches_laufend": [{"id": b["id"], "runde": b["runde"], "status": b["status"],
                                     "anfragen": len(b["anfragen"]), "gestartet": b["gestartet"]} for b in laufend],
                "abgeschlossen": offen == 0 and not laufend}

    def warten(self, intervall: float = 60.0, on_status=None) -> dict:
        """weiter() wiederholen, bis alle Materialien fertig oder fehlgeschlagen sind."""
        while True:
            st = self.weiter()
            if on_status:
                on_status(st)
            if st["abgeschlossen"]:
                return st
            time.sleep(intervall)


def status_zeile(st: dict) -> str:
    laufend = ", ".join(f"{b['status']} ({b['anfragen']})" for b in st["batches_laufend"]) or "keine"
    stufen = ", ".join(f"{k} {v}" for k, v in st["offen_je_stufe"].items()) or "–"
    return (f"Runde {st['runde']}: {st['fertig']} von {st['materialien']} fertig, {st['fehlgeschlagen']} "
            f"fehlgeschlagen, offen je Stufe: {stufen}; Batches unterwegs: {laufend}")
