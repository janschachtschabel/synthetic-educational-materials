"""Themen-Abstraktion aus WirLernenOnline-Datensätzen (python -m sem wlo-themen).

Ein LLM leitet aus Titel und Beschreibung eines WLO-Datensatzes das fachliche Thema ab (Nominalphrase wie ein
Lehrplan-Schlagwort), dazu ein allgemeineres Thema, Fachbegriffe und die Typen weggelassener Eigennamen (Produkt,
Plattform, …). Gelesen werden nur Node-ID, Titel (bis 200 Zeichen), Beschreibung (bis 800 Zeichen), Fach
(ccm:taxonid, ccm:oeh_taxonid_university) und Bildungsstufe (ccm:educationalcontext); WLO-Werte „null“ zählen als
leer. Titel und Beschreibungen stehen in keiner Ausgabe, nur auf Wunsch in einer lokalen Prüfdatei
(vergleich_lokal.jsonl, nie committen).

Dubletten (gleicher normalisierter Titel und gleiche normalisierte Beschreibung, so wie das Modell sie sieht) werden
vorab zusammengefasst: Eine Anfrage je Gruppe, alle Node-IDs der Gruppe bleiben erhalten.

Die Anfragen laufen über die Batch-Schnittstelle (halber Preis). Der Stand liegt in <ausgabe>.batch/stand.json;
jeder Aufruf holt Fertiges ab und schickt die nächste Runde, ein abgebrochener Lauf wird mit demselben Befehl
fortgesetzt. Kopierprüfung: Enthält das Thema eine Folge von vier Wörtern aus Titel oder Beschreibung (Fachbegriffe
ausgenommen) oder ähnelt es dem Titel zu stark (> 0,8), wird einmal neu gefragt; bleibt es eine Kopie, steht es mit
"kopie": true in der Ausgabe und wird nicht als Seed genutzt.

Datensätze ohne Bildungsstufe oder Fach (--ohne-stufe): vorschlag (Standard: das Modell schlägt fehlendes Fach und
fehlende Stufe aus dem OEH-Vokabular vor; markiert als "vorschlag": true), nur-thema (Thema ohne Vorschlag) oder
weglassen (Datensätze ohne Stufe fallen weg). Das Vokabular steht in einem festen Prompt-Anfang, damit die API es
zwischenspeichert. Zum Prüfen der Vorschläge: --pool maskiert fragt Datensätze mit bekanntem Fach und bekannter Stufe
so, als fehlten beide, und vergleicht (diese Ergebnisse werden nie als Seed genutzt); --pool ohne_stufe zieht die
Stichprobe nur aus Datensätzen ohne Stufe.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

from . import paths
from .llm import LLMConfig, _zeilenende, env_namen, json_aus_text, read_done, sperre_nehmen, tokens_aus_usage

ORDNER = "wlo_abstraktion"
P = "properties."
CTX = "http://w3id.org/openeduhub/vocabs/educationalContext/"
HFS = "hochschulfaechersystematik"
TITEL_MAX, BESCHR_MAX = 200, 800
OHNE_STUFE = ("vorschlag", "nur-thema", "weglassen")
POOLS = ("alle", "ohne_stufe", "maskiert")
ENTFERNT_TYPEN = ("produkt", "marke", "software", "plattform", "verlag", "reihe", "kanal", "sendung", "figur", "autor")
# Reihenfolge der Stufen für die geschichtete Stichprobe (maßgeblich ist die erste Stufe eines Datensatzes)
STUFEN_REIHE = ["grundschule", "sekundarstufe_1", "sekundarstufe_2", "schule", "foerderschule", "hochschule",
                "berufliche_bildung", "erwachsenenbildung", "elementarbereich", "fortbildung", "informelles_lernen",
                "fernunterricht"]
SCHWELLE_AEHNLICHKEIT = 0.8
FORMWOERTER = re.compile(r"\b(arbeitsblatt|video|quiz|übung|kurs|lektion|klasse|unterricht|material|lösung|"
                         r"einführung|erklärung)\w*", re.I)
# Preise in US-Dollar je 1 Mio. Tokens über die Batch-Schnittstelle (Eingabe, davon aus dem Cache, Ausgabe)
PREISE = {"gpt-6-luna": {"eingabe": 0.05, "cache": 0.005, "ausgabe": 0.25}}
MAX_ANFRAGEN = 50_000             # je Batch-Datei
MAX_BYTES = 190 * 1024 ** 2       # je Batch-Datei
MAX_API_FEHLER = 3
KOPIE_HINWEIS = ("\n\nHinweis: Eine frühere Antwort übernahm Formulierungen aus Titel oder Beschreibung "
                 "(„{thema}“). Benenne den Lerngegenstand allgemeiner und mit eigenen Worten.")


def ordner() -> Path:
    return paths.lokal_pfad(ORDNER)


# --- Lesen ---------------------------------------------------------------------------------------------

def text(v: str | None) -> str:
    """Textfeld ohne WLO-Platzhalter „null“."""
    v = (v or "").strip()
    return "" if v.casefold() == "null" else v


def liste(v: str | None) -> list[str]:
    """Kommagetrennte Liste ohne leere Einträge und ohne „null“ (WLO schreibt das auch in Listen)."""
    return [x.strip() for x in (v or "").split(",") if x.strip() and x.strip().casefold() != "null"]


def norm(s: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", (s or "").casefold()).split())


def schluessel(titel: str, beschreibung: str) -> str:
    """Dublettenschlüssel: normalisierter Titel und normalisierte Beschreibung (gekürzt wie im Prompt)."""
    roh = norm(titel[:TITEL_MAX]) + "\x1f" + norm(beschreibung[:BESCHR_MAX])
    return "w" + hashlib.sha1(roh.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def vokabulare():
    from .vocabs import load

    return {"discipline": load("discipline"), "educationalContext": load("educationalContext"),
            HFS: load(HFS)}


def ref(uri: str) -> dict:
    """{"uri", "label"} aus dem passenden OEH-Vokabular."""
    for v in vokabulare().values():
        if uri in v.by_uri:
            return {"uri": uri, "label": v.by_uri[uri]["label"]}
    return {"uri": uri, "label": uri.rsplit("/", 1)[-1]}


def lesen(pfad: Path, delimiter: str = ";") -> Iterator[dict]:
    """Datensätze mit Titel oder Beschreibung: {node, titel, beschreibung, faecher, stufen} (Fach und Stufe als URIs
    der OEH-Vokabulare; andere Werte, z. B. „legal“ in ccm:educationalcontext, werden verworfen)."""
    csv.field_size_limit(10 ** 9)
    vok = vokabulare()
    fach_uris = set(vok["discipline"].by_uri) | set(vok[HFS].by_uri)
    ctx_uris = set(vok["educationalContext"].by_uri)
    with Path(pfad).open(encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f, delimiter=delimiter)
        spalte = {k: (P + k if P + k in r.fieldnames else k) for k in
                  ("sys:node-uuid", "cclom:title", "cclom:general_description", "ccm:taxonid",
                   "ccm:oeh_taxonid_university", "ccm:educationalcontext")}
        hat_title = "title" in r.fieldnames
        for row in r:
            titel = text(row.get(spalte["cclom:title"])) or (text(row.get("title")) if hat_title else "")
            beschr = text(row.get(spalte["cclom:general_description"]))
            if not (titel or beschr):
                continue
            faecher = [u for u in liste(row.get(spalte["ccm:taxonid"])) + liste(row.get(spalte["ccm:oeh_taxonid_university"]))
                       if u in fach_uris]
            stufen = [u for u in liste(row.get(spalte["ccm:educationalcontext"])) if u in ctx_uris]
            yield {"node": text(row.get(spalte["sys:node-uuid"])), "titel": titel[:TITEL_MAX],
                   "beschreibung": beschr[:BESCHR_MAX], "faecher": list(dict.fromkeys(faecher)),
                   "stufen": list(dict.fromkeys(stufen))}


def gruppieren(datensaetze: Iterable[dict]) -> dict[str, dict]:
    """Dubletten zusammenfassen: id → {id, node_ids, faecher, stufen, titel, beschreibung} (Fächer und Stufen aller
    Datensätze der Gruppe, Text vom ersten)."""
    out: dict[str, dict] = {}
    for d in datensaetze:
        gid = schluessel(d["titel"], d["beschreibung"])
        g = out.get(gid)
        if g is None:
            out[gid] = {"id": gid, "node_ids": [d["node"]] if d["node"] else [], "faecher": list(d["faecher"]),
                        "stufen": list(d["stufen"]), "titel": d["titel"], "beschreibung": d["beschreibung"]}
            continue
        if d["node"] and d["node"] not in g["node_ids"]:
            g["node_ids"].append(d["node"])
        g["faecher"] += [u for u in d["faecher"] if u not in g["faecher"]]
        g["stufen"] += [u for u in d["stufen"] if u not in g["stufen"]]
    return out


def erste_stufe(g: dict) -> str:
    ids = [u.removeprefix(CTX) for u in g["stufen"]]
    return min(ids, key=lambda s: STUFEN_REIHE.index(s) if s in STUFEN_REIHE else 99) if ids else "ohne_stufe"


def auswaehlen(gruppen: dict[str, dict], ohne_stufe: str = "vorschlag", limit: int | None = None,
               zufall: int = 42, pool: str = "alle") -> list[str]:
    """IDs der zu bearbeitenden Gruppen. Mit limit eine geschichtete Stichprobe: gleich viele je Stufe (erste Stufe des
    Datensatzes; was eine Stufe nicht füllt, geht an die übrigen), innerhalb der Stufe reihum je Fach."""
    if ohne_stufe not in OHNE_STUFE:
        raise ValueError(f"--ohne-stufe: {ohne_stufe!r}, möglich: {', '.join(OHNE_STUFE)}")
    if pool not in POOLS:
        raise ValueError(f"--pool: {pool!r}, möglich: {', '.join(POOLS)}")
    ids = [gid for gid, g in gruppen.items() if g["stufen"] or ohne_stufe != "weglassen"]
    if pool == "ohne_stufe":
        ids = [gid for gid in ids if not gruppen[gid]["stufen"]]
    elif pool == "maskiert":
        ids = [gid for gid in ids if gruppen[gid]["stufen"] and gruppen[gid]["faecher"]]
    if not limit or limit >= len(ids):
        return sorted(ids)
    rnd = random.Random(zufall)
    schichten: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for gid in sorted(ids):
        g = gruppen[gid]
        schichten[erste_stufe(g)][(g["faecher"] or ["ohne_fach"])[0]].append(gid)
    reihen = {}
    for st, je_fach in schichten.items():  # je Stufe: reihum je Fach (Fächer und Datensätze zufällig geordnet)
        for v in je_fach.values():
            rnd.shuffle(v)
        faecher = sorted(je_fach)
        rnd.shuffle(faecher)
        reihe = []
        while any(je_fach[f] for f in faecher):
            reihe += [je_fach[f].pop() for f in faecher if je_fach[f]]
        reihen[st] = reihe
    out: list[str] = []
    offen = sorted(reihen)
    while len(out) < limit and offen:
        je = max(1, (limit - len(out)) // len(offen))
        for st in list(offen):
            nimm = reihen[st][:min(je, limit - len(out))]
            reihen[st] = reihen[st][len(nimm):]
            out += nimm
            if not reihen[st]:
                offen.remove(st)
            if len(out) >= limit:
                break
    return sorted(out)


# --- Prompt ---------------------------------------------------------------------------------------------

def system_prompt(vorschlag: bool = False) -> str:
    base = (paths.PROMPT_DIR / "wlo_themen.md").read_text(encoding="utf-8").strip()
    if not vorschlag:
        return base
    regeln = (paths.PROMPT_DIR / "wlo_themen_vorschlag.md").read_text(encoding="utf-8").strip()
    return f"{base}\n{regeln}\n\n{vokabular_text()}"


@lru_cache(maxsize=1)
def vokabular_text() -> str:
    """Listen für Fach- und Stufenvorschläge (fester Prompt-Anfang, wird von der API zwischengespeichert)."""
    v = vokabulare()
    teile = ["SCHULFÄCHER (label<TAB>uri):"] + [f"{c['label']}\t{c['uri']}" for c in v["discipline"].concepts]
    teile += ["", "STUDIENFÄCHER (Ebene<TAB>label<TAB>uri):"] + [f"{c['level']}\t{c['label']}\t{c['uri']}"
                                                                for c in v[HFS].concepts if c["level"] >= 1]
    teile += ["", "BILDUNGSBEREICHE (label<TAB>uri):"] + [f"{c['label']}\t{c['uri']}"
                                                         for c in v["educationalContext"].concepts]
    return "\n".join(teile)


def prompt_hash(ohne_stufe: str) -> str:
    return hashlib.sha256((system_prompt(False) + "\0" + system_prompt(ohne_stufe == "vorschlag") + "\0"
                           + KOPIE_HINWEIS).encode()).hexdigest()[:12]


def braucht_vorschlag(g: dict, ohne_stufe: str) -> bool:
    return ohne_stufe == "vorschlag" and (not g["faecher"] or not g["stufen"])


def anfrage(g: dict, ohne_stufe: str, vorige: str | None = None) -> tuple[str, str]:
    """(System, User) für eine Gruppe; vorige = Thema einer als Kopie verworfenen Antwort."""
    user = json.dumps({"fach": [ref(u)["label"] for u in g["faecher"]] or None,
                       "bildungsbereich": [ref(u)["label"] for u in g["stufen"]] or None,
                       "titel": g["titel"], "beschreibung": g["beschreibung"]}, ensure_ascii=False)
    if vorige:
        user += KOPIE_HINWEIS.format(thema=vorige)
    return system_prompt(braucht_vorschlag(g, ohne_stufe)), user


# --- Prüfung --------------------------------------------------------------------------------------------

def _maske(thema: str, fachbegriffe: list[str]) -> list[str | None]:
    """Wörter des Themas; Wörter, die zu einem Fachbegriff gehören, als None."""
    w: list[str | None] = list(norm(thema).split())
    for fb in sorted(fachbegriffe, key=lambda x: -len(norm(x).split())):
        f = norm(fb).split()
        if not f:
            continue
        for i in range(len(w) - len(f) + 1):
            if w[i:i + len(f)] == f:
                w[i:i + len(f)] = [None] * len(f)
    return w


def vier_woerter(thema: str, quelle: str, fachbegriffe: list[str] = ()) -> bool:
    """Ob das Thema eine Folge von vier Wörtern aus quelle enthält (Wörter von Fachbegriffen zählen nicht)."""
    w = _maske(thema, list(fachbegriffe))
    q = norm(quelle).split()
    folgen = {tuple(q[i:i + 4]) for i in range(len(q) - 3)}
    return any(None not in w[i:i + 4] and tuple(w[i:i + 4]) in folgen for i in range(len(w) - 3))


def kopierpruefung(thema: str, fachbegriffe: list[str], titel: str, beschreibung: str) -> dict:
    k_t = vier_woerter(thema, titel, fachbegriffe)
    k_b = vier_woerter(thema, beschreibung, fachbegriffe)
    ae = round(SequenceMatcher(None, norm(thema), norm(titel)).ratio(), 3) if titel else 0.0
    return {"kopie_4w_titel": k_t, "kopie_4w_beschreibung": k_b, "aehnlichkeit_titel": ae,
            "kopie": k_t or k_b or ae > SCHWELLE_AEHNLICHKEIT}


def vorschlag_pruefen(v, vokabular: str) -> dict | None:
    """Vorschlag {"label", "uri"} nur, wenn beides exakt einem Eintrag des Vokabulars entspricht."""
    if not isinstance(v, dict):
        return None
    c = vokabulare()[vokabular].by_uri.get(str(v.get("uri", "")).strip())
    if not c or c["label"] != str(v.get("label", "")).strip():
        return None
    return {"uri": c["uri"], "label": c["label"]}


def auswerten(d, g: dict, ohne_stufe: str) -> dict:
    """Antwort des Modells → Ergebnisfelder (ohne Quelltexte). ValueError bei unbrauchbarer Antwort."""
    if not isinstance(d, dict) or not isinstance(d.get("thema"), str) or not d["thema"].strip():
        raise ValueError("Antwort ohne Thema")
    thema = " ".join(d["thema"].split())
    ta = d.get("thema_allgemeiner")
    ta = " ".join(ta.split()) if isinstance(ta, str) and ta.strip() and ta.strip().casefold() != "null" else None
    fb = list(dict.fromkeys(" ".join(x.split()) for x in d.get("fachbegriffe") or []
                            if isinstance(x, str) and x.strip()))[:5]
    entfernt = sorted({x.strip().casefold() for x in d.get("entfernt") or [] if isinstance(x, str) and x.strip()})
    out = {"thema": thema, "thema_allgemeiner": ta, "fachbegriffe": fb,
           "entfernt": [e if e in ENTFERNT_TYPEN else "sonstige" for e in entfernt]}
    faecher = [ref(u) for u in g["faecher"]]
    stufen = [ref(u) for u in g["stufen"]]
    vorschlag = {}
    if braucht_vorschlag(g, ohne_stufe):
        if not stufen:
            s = vorschlag_pruefen(d.get("stufe_vorschlag"), "educationalContext")
            if s:
                stufen, vorschlag["stufe"] = [s], True
        if not faecher:
            hs = any(s["uri"] == CTX + "hochschule" for s in stufen)
            f = vorschlag_pruefen(d.get("fach_vorschlag"), HFS if hs else "discipline") or \
                vorschlag_pruefen(d.get("fach_vorschlag"), "discipline" if hs else HFS)
            if f:
                faecher, vorschlag["fach"] = [f], True
    out.update(fach=faecher, stufe=stufen, vorschlag=bool(vorschlag))
    if vorschlag:
        out["vorschlag_felder"] = sorted(vorschlag)
    if braucht_vorschlag(g, ohne_stufe):  # erbeten, aber nicht exakt aus dem Vokabular
        fehlt = [k for k, leer in (("fach", not g["faecher"]), ("stufe", not g["stufen"])) if leer and k not in vorschlag]
        if fehlt:
            out["vorschlag_ungueltig"] = fehlt
    return out


# --- Batch-Lauf ----------------------------------------------------------------------------------------

def maskiert(g: dict) -> dict:
    """Gruppe ohne Fach und Stufe (zum Prüfen der Vorschläge)."""
    return {**g, "faecher": [], "stufen": []}


def standard_ausgabe(limit: int | None, ohne_stufe: str, pool: str = "alle") -> Path:
    name = "abstraktion" + (f"_n{limit}" if limit else "") + ("" if ohne_stufe == "vorschlag" else f"_{ohne_stufe}") \
        + ("" if pool == "alle" else f"_{pool}")
    return ordner() / f"{name}.jsonl"


class WloThemenLauf:
    """Themen-Abstraktion über die Batch-Schnittstelle, fortsetzbar wie sem.batch.BatchLauf."""

    def __init__(self, gruppen: dict[str, dict], cfg: LLMConfig, out_path: Path | None = None,
                 limit: int | None = None, ohne_stufe: str = "vorschlag", zufall: int = 42,
                 vergleich: bool = False, client=None, pool: str = "alle", sofort: bool = False):
        from .batch import AnthropicBatches, OpenAIBatches, OpenAISofort
        from .llm import FLEX_TIMEOUT

        self.gruppen = gruppen
        self.cfg = cfg
        self.ohne_stufe = ohne_stufe
        self.pool = pool
        if pool == "maskiert" and ohne_stufe != "vorschlag":
            raise ValueError("--pool maskiert prüft die Vorschläge: nur mit --ohne-stufe vorschlag")
        self.ids = auswaehlen(gruppen, ohne_stufe, limit, zufall, pool)
        self.out_path = Path(out_path or standard_ausgabe(limit, ohne_stufe, pool))
        self.err_path = self.out_path.with_suffix(".fehler.jsonl")
        self.vergleich_path = self.out_path.with_name(self.out_path.stem + ".vergleich_lokal.jsonl") \
            if vergleich else None
        self.ordner = self.out_path.with_name(self.out_path.stem + ".batch")
        self.stand_path = self.ordner / "stand.json"
        cfg.pruefen()
        if client is None:
            if not cfg.api_key:
                raise ValueError(f"Kein API-Key: {env_namen(cfg.anbieter)['api_key']} setzen.")
            if cfg.anbieter == "anthropic":
                from anthropic import Anthropic

                client = Anthropic(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout)
            else:
                from openai import OpenAI

                timeout = max(cfg.timeout, FLEX_TIMEOUT) if sofort and cfg.service_tier == "flex" else cfg.timeout
                client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout, max_retries=0 if sofort
                                else 2)
        if sofort and cfg.anbieter == "anthropic":
            raise ValueError("--sofort gibt es nur für OpenAI-kompatible APIs")
        if sofort:
            self.api = OpenAISofort(client, cfg, self.ordner, cfg.workers)
        else:
            self.api = AnthropicBatches(client) if cfg.anbieter == "anthropic" else OpenAIBatches(client)
        parameter = {"modell": cfg.model, "anbieter": cfg.anbieter, "reasoning_effort": cfg.reasoning_effort,
                     "prompt_hash": prompt_hash(ohne_stufe), "limit": limit, "ohne_stufe": ohne_stufe,
                     "zufall": zufall, "gruppen": len(self.ids)}
        if pool != "alle":
            parameter["pool"] = pool
        if self.stand_path.exists():
            self.stand = json.loads(self.stand_path.read_text(encoding="utf-8"))
            if self.stand["parameter"] != parameter:
                raise ValueError(f"{self.ordner.name} gehört zu einem Lauf mit anderen Einstellungen "
                                 f"({self.stand['parameter']}); für einen neuen Lauf --out ändern.")
        else:
            self.stand = {"gestartet": time.time(), "parameter": parameter, "runde": 0, "offen": {},
                          "fehlgeschlagen": [], "batches": [], "tokens": {}, "token_budget": None,
                          "pool": {"gruppen": len(gruppen), "gruppen_mit_stufe": sum(bool(g["stufen"])
                                                                                     for g in gruppen.values()),
                                   "gruppen_auswahl": len(auswaehlen(gruppen, ohne_stufe)),
                                   "datensaetze": sum(len(g["node_ids"]) or 1 for g in gruppen.values())}}

    def _speichern(self):
        self.ordner.mkdir(parents=True, exist_ok=True)
        tmp = self.stand_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.stand, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.stand_path)

    @contextmanager
    def _sperre(self):
        lock = self.out_path.with_suffix(".lock")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        sperre_nehmen(lock)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _erledigt(self) -> set[str]:
        return read_done(self.out_path) | set(self.stand["fehlgeschlagen"])

    def weiter(self) -> dict:
        with self._sperre():
            self._done = self._erledigt()  # einmal lesen, dann fortschreiben (Ausgabe kann groß werden)
            for b in self.stand["batches"]:
                if not b["ausgewertet"]:
                    self._abholen(b)
            self._abschicken()
            self._speichern()
        return self.status()

    def warten(self, intervall: float = 60.0, on_status=None) -> dict:
        while True:
            st = self.weiter()
            if on_status:
                on_status(st)
            if st["abgeschlossen"]:
                return st
            time.sleep(intervall)

    def _abholen(self, b: dict):
        status, zeilen, grund = self.api.abrufen(b["id"], b["anfragen"])
        b["status"] = status
        if zeilen is None:
            return
        for r in zeilen:
            self._ergebnis(r)
        if status == "failed" and not zeilen and "token_limit" in grund:
            self.stand["token_budget"] = max(1, int((self.stand["token_budget"] or b.get("tokens", 0)) / 2))
        b.update(ausgewertet=True, anzahl=len(b["anfragen"]), anfragen=[])
        self._speichern()

    def _ergebnis(self, r: dict):
        gid, versuch = r["custom_id"].split("|")[:2]
        if gid not in self.gruppen or gid in self._done:
            return
        z = self.stand["offen"].setdefault(gid, {"versuch": 0})
        resp = r.get("response") or {}
        if resp.get("status_code") != 200:
            z["api_fehler"] = z.get("api_fehler", 0) + 1
            if z["api_fehler"] >= MAX_API_FEHLER:
                self._fehlgeschlagen(gid, str((resp.get("body") or {}).get("error") or resp.get("status_code"))[:300])
            return
        body = resp["body"]
        t = tokens_aus_usage(body.get("usage"))
        tok = self.stand["tokens"]
        for k, v in t.items():
            tok[k] = tok.get(k, 0) + v
        z["tokens"] = {k: z.get("tokens", {}).get(k, 0) + v for k, v in t.items()}
        if r.get("standardpreis"):  # sofort ohne Flex: doppelter Preis, für die Kosten getrennt gezählt
            std = self.stand.setdefault("tokens_standardpreis", {})
            for k, v in t.items():
                std[k] = std.get(k, 0) + v
        g = self.gruppen[gid]
        wahr = g
        if self.pool == "maskiert":
            g = maskiert(g)
        try:
            erg = auswerten(json_aus_text(body["choices"][0]["message"].get("content") or ""), g, self.ohne_stufe)
        except (ValueError, KeyError, TypeError) as e:
            z["ungueltig"] = z.get("ungueltig", 0) + 1
            if z["ungueltig"] >= MAX_API_FEHLER:
                self._fehlgeschlagen(gid, f"unbrauchbare Antwort: {e}")
            return
        pruef = kopierpruefung(erg["thema"], erg["fachbegriffe"], g["titel"], g["beschreibung"])
        if pruef["kopie"] and int(versuch) == 0:
            z.update(versuch=1, vorige=erg["thema"])  # einmal neu fragen
            return
        zeile = {"id": gid, "node_ids": g["node_ids"], **erg, "pruefung": pruef,
                 "seed_tauglich": not pruef["kopie"], "versuche": int(versuch) + 1, "tokens": z["tokens"]}
        if self.pool == "maskiert":  # Vergleich mit den echten Werten; nie als Seed
            zeile.update(seed_tauglich=False, maskiert={"fach": [ref(u) for u in wahr["faecher"]],
                                                       "stufe": [ref(u) for u in wahr["stufen"]]})
        _zeilenende(self.out_path)
        with self.out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        if self.vergleich_path:
            with self.vergleich_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": gid, "thema": erg["thema"], "titel": g["titel"],
                                    "beschreibung": g["beschreibung"][:300], **pruef}, ensure_ascii=False) + "\n")
        self.stand["offen"].pop(gid, None)
        self._done.add(gid)

    def _fehlgeschlagen(self, gid: str, grund: str):
        self.stand["offen"].pop(gid, None)
        self.stand["fehlgeschlagen"].append(gid)
        self._done.add(gid)
        _zeilenende(self.err_path)
        with self.err_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"id": gid, "fehler": grund}, ensure_ascii=False) + "\n")

    def _abschicken(self):
        unterwegs = {c.split("|")[0] for b in self.stand["batches"] if not b["ausgewertet"] for c in b["anfragen"]}
        erledigt = self._done
        budget = self.stand["token_budget"]
        tokens = sum(b.get("tokens", 0) for b in self.stand["batches"] if not b["ausgewertet"])
        runde = self.stand["runde"] + 1
        zeilen = []
        for gid in self.ids:
            if gid in erledigt or gid in unterwegs:
                continue
            z = self.stand["offen"].get(gid, {})
            g = maskiert(self.gruppen[gid]) if self.pool == "maskiert" else self.gruppen[gid]
            system, user = anfrage(g, self.ohne_stufe, z.get("vorige"))
            n = (len(system) + len(user)) // 3 + 1
            if budget and tokens + n > budget and (zeilen or tokens):
                break
            body = self.cfg.parameter(system, user, json_mode=True, batch=True)
            tokens += n
            zeilen.append((f"{gid}|{z.get('versuch', 0)}|{runde}", body, n,
                           len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 200))
        if not zeilen:
            return
        self.stand["runde"] = runde
        self.ordner.mkdir(parents=True, exist_ok=True)
        teil, groesse, nr = [], 0, 0
        for cid, body, t, n in zeilen + [(None, None, 0, 0)]:
            if teil and (body is None or len(teil) >= MAX_ANFRAGEN or groesse + n > MAX_BYTES):
                nr += 1
                datei = self.ordner / f"runde{runde:03d}_{nr:02d}.jsonl"
                bid, status = self.api.anlegen(datei, runde, [(c, b) for c, b, _ in teil])
                self.stand["batches"].append({"id": bid, "runde": runde, "datei": datei.name, "status": status,
                                              "gestartet": datetime.now().isoformat(timespec="seconds"),
                                              "anfragen": [c for c, _, _ in teil],
                                              "tokens": sum(x for _, _, x in teil), "ausgewertet": False})
                self._speichern()
                teil, groesse = [], 0
            if body is not None:
                teil.append((cid, body, t))
                groesse += n

    def status(self) -> dict:
        fertig = len(read_done(self.out_path) & set(self.ids))
        laufend = [b for b in self.stand["batches"] if not b["ausgewertet"]]
        offen = len(self.ids) - fertig - len(self.stand["fehlgeschlagen"])
        return {"gruppen": len(self.ids), "fertig": fertig, "fehlgeschlagen": len(self.stand["fehlgeschlagen"]),
                "offen": offen, "wiederholung_kopie": sum(1 for z in self.stand["offen"].values() if z.get("vorige")),
                "runde": self.stand["runde"], "tokens": self.stand["tokens"],
                "kosten_usd": kosten_lauf(self.stand, self.cfg.model),
                "batches_laufend": [{"id": b["id"], "status": b["status"], "anfragen": len(b["anfragen"])}
                                    for b in laufend],
                "abgeschlossen": offen == 0 and not laufend}


def status_zeile(st: dict) -> str:
    laufend = ", ".join(f"{b['status']} ({b['anfragen']})" for b in st["batches_laufend"]) or "keine"
    k = st["kosten_usd"]
    return (f"Runde {st['runde']}: {st['fertig']} von {st['gruppen']} Gruppen fertig, {st['fehlgeschlagen']} "
            f"fehlgeschlagen, {st['wiederholung_kopie']} werden wegen Kopie neu gefragt; Batches unterwegs: {laufend}"
            + (f"; bisher {k:.3f} $" if k is not None else ""))


# --- Kennzahlen ----------------------------------------------------------------------------------------

def vorschlag_kennzahlen(rows: list[dict]) -> dict:
    """Fach- und Stufenvorschläge: Zahl, ungültige, Verteilung; bei maskierten Gruppen Treffer gegen die echten Werte
    (Treffer = vorgeschlagener Wert ist einer der Werte des Datensatzes; zusätzlich gezählt: Ober- oder Unterbegriff in
    der Hochschulfächersystematik)."""
    mit = [x for x in rows if x.get("vorschlag")]
    out = {"gruppen_mit_vorschlag": len(mit),
           "fach": sum("fach" in x.get("vorschlag_felder", []) for x in rows),
           "stufe": sum("stufe" in x.get("vorschlag_felder", []) for x in rows),
           "ungueltig": dict(Counter(k for x in rows for k in x.get("vorschlag_ungueltig", []))),
           "stufe_verteilung": dict(Counter(x["stufe"][0]["label"] for x in mit
                                            if "stufe" in x.get("vorschlag_felder", [])).most_common()),
           "fach_top": dict(Counter(x["fach"][0]["label"] for x in mit
                                    if "fach" in x.get("vorschlag_felder", [])).most_common(15))}
    mask = [x for x in rows if x.get("maskiert")]
    if mask:
        v = vokabulare()[HFS]

        def verwandt(a, b):
            return a == b or (a in v.by_uri and b in v.by_uri and (a in v.ancestors(b) or b in v.ancestors(a)))

        f = [x for x in mask if "fach" in x.get("vorschlag_felder", [])]
        s = [x for x in mask if "stufe" in x.get("vorschlag_felder", [])]
        out["maskiert"] = {
            "gruppen": len(mask), "fach_vorgeschlagen": len(f), "stufe_vorgeschlagen": len(s),
            "fach_treffer": sum(x["fach"][0]["uri"] in {y["uri"] for y in x["maskiert"]["fach"]} for x in f),
            "fach_treffer_mit_ober_unterbegriff": sum(any(verwandt(x["fach"][0]["uri"], y["uri"])
                                                          for y in x["maskiert"]["fach"]) for x in f),
            "stufe_treffer": sum(x["stufe"][0]["uri"] in {y["uri"] for y in x["maskiert"]["stufe"]} for x in s),
            "stufe_fehler_top": dict(Counter(x["stufe"][0]["label"] + " statt " +
                                             ", ".join(y["label"] for y in x["maskiert"]["stufe"])
                                             for x in s if x["stufe"][0]["uri"] not in
                                             {y["uri"] for y in x["maskiert"]["stufe"]}).most_common(10)),
        }
    return out


def kosten(tokens: dict, modell: str) -> float | None:
    p = PREISE.get(modell)
    if not p or not tokens:
        return None
    ein, cache, aus = tokens.get("eingabe", 0), tokens.get("davon_cache", 0), tokens.get("ausgabe", 0)
    return round(((ein - cache) * p["eingabe"] + cache * p["cache"] + aus * p["ausgabe"]) / 1e6, 4)


def kosten_lauf(stand: dict, modell: str) -> float | None:
    """Kosten eines Laufs: alle Tokens zum Batch-Preis, Anfragen zum Standardpreis (sofort ohne Flex) doppelt."""
    k = kosten(stand.get("tokens") or {}, modell)
    extra = kosten(stand.get("tokens_standardpreis") or {}, modell)
    return round(k + (extra or 0), 4) if k is not None else None


def lade_jsonl(p: Path) -> list[dict]:
    out = []
    for z in Path(p).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(z))
        except json.JSONDecodeError:
            pass  # halbe Zeile nach Absturz
    return out


def kennzahlen(out_path: Path, stand: dict | None = None, gnd: dict | None = None,
               pool_gruppen: int | None = None, modell: str | None = None) -> dict:
    """Kennzahlen wie im Testlauf themen_test_v2, dazu Tokens je Gruppe, Kosten und Hochrechnung."""
    from .wlo_seeds import gnd_index

    rows = lade_jsonl(out_path)
    stand = stand or {}
    gnd = gnd_index() if gnd is None else gnd
    n = len(rows)
    tn = Counter(norm(x["thema"]) for x in rows)
    tan = Counter(norm(x["thema_allgemeiner"]) for x in rows if x.get("thema_allgemeiner"))
    fbs = [f for x in rows for f in x["fachbegriffe"]]
    tok = stand.get("tokens") or {}
    modell = modell or (stand.get("parameter") or {}).get("modell")
    k = kosten_lauf(stand, modell) if modell else None
    kb = kosten(tok, modell) if modell else None  # Hochrechnung zum Batch-Preis (Volllauf über die Batch-API)
    pool = pool_gruppen or (stand.get("pool") or {}).get("gruppen_auswahl")
    gesamt_pool = (stand.get("pool") or {}).get("gruppen")
    je = {key: round(tok.get(key, 0) / n, 1) for key in ("eingabe", "davon_cache", "ausgabe", "davon_reasoning")} \
        if n else {}
    return {
        "gruppen": n, "datensaetze": sum(len(x["node_ids"]) for x in rows),
        "seed_tauglich": sum(x["seed_tauglich"] for x in rows),
        "kopie_nach_wiederholung": sum(not x["seed_tauglich"] for x in rows),
        "wiederholt_wegen_kopie": sum(x.get("versuche", 1) > 1 for x in rows),
        "kopie_4_woerter_titel": sum(x["pruefung"]["kopie_4w_titel"] for x in rows),
        "kopie_4_woerter_beschreibung": sum(x["pruefung"]["kopie_4w_beschreibung"] for x in rows),
        "aehnlichkeit_titel_gt_0_8": sum(x["pruefung"]["aehnlichkeit_titel"] > SCHWELLE_AEHNLICHKEIT for x in rows),
        "verbotene_formwoerter": sum(bool(FORMWOERTER.search(x["thema"])) for x in rows),
        "woerter_im_schnitt": round(sum(len(x["thema"].split()) for x in rows) / max(1, n), 2),
        "laenge_ausserhalb_2_8": sum(not 2 <= len(x["thema"].split()) <= 8 for x in rows),
        "mit_allgemeiner": sum(bool(x.get("thema_allgemeiner")) for x in rows),
        "entfernt_typen": dict(Counter(e for x in rows for e in x["entfernt"]).most_common()),
        "mit_entfernung": sum(bool(x["entfernt"]) for x in rows),
        "fachbegriffe_gesamt": len(fbs), "fachbegriffe_distinct": len({norm(f) for f in fbs}),
        "fachbegriffe_gnd_exakt": sum(norm(f) in gnd for f in fbs),
        "gruppen_mit_mind_1_fachbegriff_gnd": sum(any(norm(f) in gnd for f in x["fachbegriffe"]) for x in rows),
        "gnd_exakt_thema": sum(norm(x["thema"]) in gnd for x in rows),
        "gnd_exakt_allgemeiner": sum(norm(x["thema_allgemeiner"]) in gnd for x in rows if x.get("thema_allgemeiner")),
        "themen_distinct": len(tn), "themen_mehrfach": sum(1 for v in tn.values() if v >= 2),
        "allgemeiner_distinct": len(tan), "allgemeiner_top": dict(tan.most_common(10)),
        "vorschlag": vorschlag_kennzahlen(rows),
        "ohne_fach": sum(not x["fach"] for x in rows), "ohne_stufe": sum(not x["stufe"] for x in rows),
        "tokens": tok, "tokens_je_gruppe": je, "modell": modell, "kosten_usd": k,
        "hochrechnung": {"gruppen": pool, "gruppen_gesamt_ohne_filter": gesamt_pool, "preis": "batch",
                         "kosten_usd": round(kb / n * pool, 2) if kb is not None and n and pool else None,
                         "kosten_usd_alle_gruppen": round(kb / n * gesamt_pool, 2)
                         if kb is not None and n and gesamt_pool else None},
    }
