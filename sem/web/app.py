"""Oberfläche: FastAPI liefert HTML-Seiten, HTMX tauscht Teile davon aus (Status, Formularteile, Vorschau).

Lange Arbeiten (Seeds, Materialien, Batch-Runden, Mappings) laufen als eigener Prozess (sem/prozess.py). Die Seite
liest nur deren kleine Stand-Datei und bleibt deshalb auch bei großen Läufen flüssig; ein Lauf geht weiter, wenn
die Seite geschlossen oder der Server neu gestartet wird.

Nur für diesen Rechner gedacht (127.0.0.1, keine Anmeldung): Zustandsändernde Anfragen müssen von dieser Seite
kommen (Host- und Origin-Prüfung), damit keine fremde Webseite im selben Browser Läufe starten kann.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import paths, prozess, vorschau
from .. import schema as S
from ..bereiche import BEREICHE
from ..bereiche.base import STANDARD_KONFIDENZ, STATUS_WERTE, filtergruppe
from ..generate import SPRACHEN
from ..i18n import UI_SPRACHEN, t
from ..llm import DEFAULT_MODEL, DEFAULT_WORKERS, LLMConfig, _prozess_laeuft, api_key_fuer
from ..prompts import PROMPT_FILES, placeholders, prompt_path
from . import dateien

HIER = Path(__file__).parent
app = FastAPI(title="Synthetic educational materials", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
vorlagen = Jinja2Templates(directory=HIER / "templates")

ERLAUBTE_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
STATUS_TEXT = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
               "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
               "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
GRUPPEN = [("quellen", "Quellen und Belege"), ("konfidenz", "Konfidenz (Status der Zuordnungen)"),
           ("inhalt", "Weitere Merkmale")]
EFFORTS = ["minimal", "low", "medium", "high", "Modellstandard"]


@app.middleware("http")
async def nur_von_dieser_seite(request: Request, call_next):
    """Schutz für einen Server ohne Anmeldung: fremder Host (DNS-Rebinding) oder fremde Herkunft bei Änderungen."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ERLAUBTE_HOSTS:
        return Response("Nur über 127.0.0.1 oder localhost erreichbar", status_code=403)
    if request.method not in ("GET", "HEAD"):
        herkunft = request.headers.get("origin")
        if herkunft and herkunft.split("//", 1)[-1].rsplit(":", 1)[0] not in ERLAUBTE_HOSTS:
            return Response("Anfrage von fremder Seite abgelehnt", status_code=403)
        if not request.headers.get("hx-request"):  # Formulare fremder Seiten können diesen Header nicht setzen
            return Response("Bitte über die Oberfläche bedienen", status_code=403)
    return await call_next(request)


# --- Ordner (in Tests umlenkbar) ------------------------------------------------------------------------------------
class Ordner:
    output: Path = paths.OUTPUT_DIR
    prozesse: Path | None = None  # None = prozess.PROZESS_DIR

    @classmethod
    def einstellungen(cls) -> Path:
        return cls.output / ".oberflaeche.json"


# --- Einstellungen (LLM) -----------------------------------------------------------------------------------------
@dataclasses.dataclass
class Einstellungen:
    base_url: str = ""
    model: str = ""
    workers: int = 0
    reasoning_effort: str = ""
    flex: bool = False
    retries: int = 6
    ausfall_minuten: float = 10
    embedding_modell: str = ""


_api_key: dict[str, str] = {}  # nur im Speicher, nie in einer Datei


def einstellungen() -> Einstellungen:
    env = LLMConfig.from_env()
    try:
        gespeichert = json.loads(Ordner.einstellungen().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        gespeichert = {}
    from ..mapping.embeddings import DEFAULT_EMBEDDING_MODEL

    e = Einstellungen(base_url=env.base_url or "", model=env.model or DEFAULT_MODEL,
                      workers=env.workers or DEFAULT_WORKERS, reasoning_effort=env.reasoning_effort or "Modellstandard",
                      embedding_modell=DEFAULT_EMBEDDING_MODEL)
    felder = {f.name for f in dataclasses.fields(Einstellungen)}
    return dataclasses.replace(e, **{k: v for k, v in gespeichert.items() if k in felder})


def llm_cfg(e: Einstellungen | None = None, **aenderungen) -> LLMConfig:
    e = e or einstellungen()
    env = LLMConfig.from_env()
    cfg = LLMConfig(base_url=e.base_url or None, api_key=api_key_fuer(e.base_url, _api_key.get("key"), env),
                    model=e.model, workers=int(e.workers), max_retries=int(e.retries),
                    outage_minutes=float(e.ausfall_minuten),
                    reasoning_effort=None if e.reasoning_effort == "Modellstandard" else e.reasoning_effort,
                    service_tier="flex" if e.flex else None)
    return dataclasses.replace(cfg, **aenderungen)


def key_zustand(e: Einstellungen) -> str:
    """„eingetragen“, „umgebung“ (OPENAI_API_KEY gilt für diese Base-URL), „fremde_url“ oder „fehlt“."""
    env = LLMConfig.from_env()
    if _api_key.get("key"):
        return "eingetragen"
    if env.api_key:
        return "umgebung" if (e.base_url or None) == env.base_url else "fremde_url"
    return "fehlt"


# --- Hilfen für Vorlagen -----------------------------------------------------------------------------------------
def sprache(request: Request) -> str:
    s = request.cookies.get("sprache") or os.environ.get("SEM_UI_SPRACHE", "de")
    return s if s in UI_SPRACHEN else "de"


def uebersetzer(ui: str):
    def T(text: str, **werte) -> str:  # noqa: N802 - wie in den Vorlagen
        out = t(text, ui)
        return out.format(**werte) if werte else out
    return T


def anzahl(T, n: int, eins: str, viele: str) -> str:
    return f"{n:,}".replace(",", ".") + " " + (T(eins) if n == 1 else T(viele))


def dauer(sekunden: float | None) -> str:
    if sekunden is None:
        return "–"
    s = int(sekunden)
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60}:{s % 60:02d} min"


def zahl(n) -> str:
    return f"{n:,}".replace(",", ".") if isinstance(n, int) else str(n)


def seite(request: Request, vorlage: str, **ctx) -> HTMLResponse:
    ui = sprache(request)
    T = uebersetzer(ui)
    return vorlagen.TemplateResponse(request, vorlage, {
        "T": T, "ui": ui, "UI_SPRACHEN": UI_SPRACHEN, "pfad": request.url.path,
        "voll": vorlage.startswith("seite_"), "zahl": zahl, "dauer": dauer,
        "komma": lambda x: (f"{x:.1f}" if ui == "en" else f"{x:.1f}".replace(".", ",")),
        "anzahl": lambda n, eins, viele: anzahl(T, n, eins, viele), **ctx})


def teilansicht(request: Request) -> bool:
    """HTMX tauscht nur einen Teil aus; beim Zurückblättern im Verlauf braucht es die ganze Seite."""
    h = request.headers
    return bool(h.get("hx-request")) and not h.get("hx-history-restore-request") and not h.get("hx-boosted")


def bereich(key: str):
    if key not in BEREICHE:
        raise HTTPException(404)
    return _bereiche.setdefault(key, BEREICHE[key]())


_bereiche: dict = {}


def ausgabedateien(art: str) -> list[Path]:
    return paths.ausgabedateien(art, Ordner.output)


def in_output(name: str, art: str | None = None) -> Path:
    """Datei aus output/ per Name; schützt vor Pfaden außerhalb."""
    p = (Ordner.output / Path(name).name).resolve()
    if p.parent != Ordner.output.resolve() or not p.exists() or (art and not p.name.startswith(paths.PRAEFIX[art])):
        raise HTTPException(404, "Datei nicht gefunden")
    return p


# --- Läufe (Prozesse) --------------------------------------------------------------------------------------------
PHASEN = {
    "startet": ("startet", "info", "Startet: liest die Eingabedateien (bei großen Läufen einige Sekunden) …"),
    "laeuft": ("läuft", "info", "Läuft"),
    "haelt_an": ("hält an", "warnung", "Hält an: Es startet keine neue Anfrage mehr, laufende werden beendet."),
    "haengt": ("keine Rückmeldung", "warnung", "Keine Rückmeldung seit {sekunden} s: Der Prozess lebt, schreibt aber "
               "keinen Stand. Bei sehr großen Dateien kann der Start dauern; sonst anhalten und fortsetzen."),
    "fertig": ("fertig", "erfolg", "Fertig"),
    "pausiert": ("pausiert", "warnung", "Pausiert – lässt sich fortsetzen"),
    "fehler": ("Fehler", "fehler", "Abgebrochen mit Fehler"),
}


def phase(s: dict) -> str:
    if s["laeuft"]:
        if s.get("angehalten"):
            return "haelt_an"
        if not s.get("aktualisiert"):
            return "startet"
        alter = (datetime.now() - datetime.fromisoformat(s["aktualisiert"])).total_seconds()
        return "haengt" if alter > 60 else "laeuft"
    if s.get("fehler"):
        return "fehler"
    return "fertig" if "fertig" in str(s.get("status", "")) else "pausiert"


def lauf_ansicht(name: str) -> dict | None:
    s = prozess.stand(name, Ordner.prozesse)
    if not s:
        return None
    ph = phase(s)
    alter = None
    if s.get("aktualisiert"):
        alter = int((datetime.now() - datetime.fromisoformat(s["aktualisiert"])).total_seconds())
    total = s.get("total") or 0
    bearbeitet = (s.get("done") or 0) + (s.get("failed") or 0)
    return {**s, "name": name, "phase": ph, "phase_label": PHASEN[ph][0], "ton": PHASEN[ph][1],
            "phase_text": PHASEN[ph][2], "alter": alter, "bearbeitet": bearbeitet,
            "prozent": round(100 * bearbeitet / total, 1) if total else None,
            "art": "Seeds" if name == "seeds" else "Mappings" if name.startswith("map-") else "Materialien"}


def fremde_sperre(ausgabe: Path) -> int | None:
    """PID eines anderen lebenden Prozesses auf diesem Rechner, der an der Ausgabe arbeitet (z. B. Kommandozeile)."""
    import socket

    lock = Path(ausgabe).with_suffix(".lock")
    try:
        pid, rechner = lock.read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return None
    return int(pid) if rechner == socket.gethostname() and pid.isdigit() and _prozess_laeuft(int(pid)) else None


def starten(name: str, argv: list[str], cfg: LLMConfig | None = None):
    args, env = prozess.llm_umgebung(cfg) if cfg else ([], {})
    prozess.starten(name, argv + args, env, Ordner.prozesse)


def unfertige() -> dict[str, dict]:
    from ..laeufe import unfertige_laeufe

    out = {}
    for L in unfertige_laeufe(Ordner.output):
        L["fremd_pid"] = fremde_sperre(Path(L["ausgabe"])) if L["gesperrt"] else None
        L["seeds_fehlt"] = not Path(L["seeds"]).exists()
        out[L["name"]] = L
    return out


def alle_laeufe() -> list[dict]:
    d = Ordner.prozesse or prozess.PROZESS_DIR
    namen = sorted(p.name.removesuffix(".json") for p in d.glob("*.json") if not p.name.endswith(".stand.json")) \
        if d.exists() else []
    return [v for n in namen if (v := lauf_ansicht(n))]


@app.get("/status/{name}", response_class=HTMLResponse)
def status(request: Request, name: str):
    return seite(request, "teil_status.html", lauf=lauf_ansicht(name), name=name)


@app.get("/status/{name}/daten", response_class=HTMLResponse)
def status_daten(request: Request, name: str):
    lauf = lauf_ansicht(name)
    if not lauf:
        return Response(headers={"HX-Trigger": f"lauf-ende-{name}"})
    r = seite(request, "teil_status_daten.html", lauf=lauf, name=name)
    if not lauf["laeuft"]:
        r.headers["HX-Trigger"] = f"lauf-ende-{name}"  # Knöpfe (Anhalten → Ausblenden) einmal neu zeichnen
    return r


@app.get("/laeufe/kopf", response_class=HTMLResponse)
def laeufe_kopf(request: Request):
    return seite(request, "teil_kopf_laeufe.html", laeufe=[x for x in alle_laeufe() if x["laeuft"]])


@app.post("/laeufe/{name}/anhalten", response_class=HTMLResponse)
def anhalten(request: Request, name: str):
    if prozess.laeuft(name, Ordner.prozesse):
        prozess.anhalten(name, Ordner.prozesse)
    return status(request, name)


@app.post("/laeufe/{name}/ausblenden", response_class=HTMLResponse)
def ausblenden(request: Request, name: str):
    prozess.vergessen(name, Ordner.prozesse)
    return HTMLResponse("")


@app.get("/laeufe/{name}/log", response_class=HTMLResponse)
def log(request: Request, name: str):
    d = Ordner.prozesse or prozess.PROZESS_DIR
    p = d / f"{Path(name).name}.log"
    zeilen = p.read_text(encoding="utf-8", errors="replace").splitlines()[-60:] if p.exists() else []
    return HTMLResponse(f'<pre class="log" tabindex="0">{_esc(chr(10).join(zeilen)) or "–"}</pre>')


def _esc(s: str) -> str:
    from markupsafe import escape

    return str(escape(s))


# --- Seiten ------------------------------------------------------------------------------------------------------
@app.get("/")
def start():
    return RedirectResponse("/materialien", status_code=303)


@app.get("/sprache/{code}")
def sprache_wechseln(code: str, zurueck: str = "/"):
    if not zurueck.startswith("/") or zurueck.startswith("//"):
        zurueck = "/"
    r = RedirectResponse(zurueck, status_code=303)
    if code in UI_SPRACHEN:
        r.set_cookie("sprache", code, max_age=365 * 86400, samesite="lax")
    return r


@app.get("/download")
def download(datei: str, ort: str = "output"):
    if ort == "output":
        p = in_output(datei)
    else:
        p = (paths.DATA_DIR / datei).resolve()
        if paths.DATA_DIR.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
    return FileResponse(p, filename=p.name)


# --- Einstellungen -------------------------------------------------------------------------------------------------
@app.get("/einstellungen", response_class=HTMLResponse)
def einstellungen_seite(request: Request, gespeichert: bool = False):
    e = einstellungen()
    return seite(request, "seite_einstellungen.html", e=e, efforts=EFFORTS, key=key_zustand(e),
                 env_url=LLMConfig.from_env().base_url, gespeichert=gespeichert)


@app.post("/einstellungen")
def einstellungen_speichern(base_url: str = Form(""), model: str = Form(""), api_key: str = Form(""),
                            key_loeschen: bool = Form(False), workers: int = Form(DEFAULT_WORKERS),
                            reasoning_effort: str = Form("low"), flex: bool = Form(False), retries: int = Form(6),
                            ausfall_minuten: float = Form(10)):
    e = Einstellungen(base_url=base_url.strip(), model=model.strip() or DEFAULT_MODEL,
                      workers=max(1, min(1000, workers)),
                      reasoning_effort=reasoning_effort if reasoning_effort in EFFORTS else "low", flex=flex,
                      retries=max(1, min(20, retries)), ausfall_minuten=max(1, min(240, ausfall_minuten)),
                      embedding_modell=einstellungen().embedding_modell)  # wird je Mapping-Lauf gewählt
    Ordner.output.mkdir(parents=True, exist_ok=True)
    Ordner.einstellungen().write_text(json.dumps(dataclasses.asdict(e), ensure_ascii=False, indent=1), encoding="utf-8")
    if key_loeschen:
        _api_key.pop("key", None)
    elif api_key.strip():
        _api_key["key"] = api_key.strip()
    return Response(status_code=204, headers={"HX-Redirect": "/einstellungen?gespeichert=1"})


# --- Seeds -----------------------------------------------------------------------------------------------------
def seeds_formular(form) -> dict:
    """Auswahl aus dem Formular: Bereiche, Anzahl, Gleichverteilung, Filter, Startwert, Name."""
    gewaehlt = [k for k in BEREICHE if form.get(f"an-{k}")]
    counts, balance, auswahl, themen = {}, {}, {}, {}
    for k in gewaehlt:
        b = bereich(k)
        try:
            counts[k] = max(0, int(form.get(f"n-{k}") or 0))
        except ValueError:
            counts[k] = 0
        bal = form.get(f"bal-{k}")
        balance[k] = bal if bal in b.balance_options else b.default_balance
        filt = b.auswahl_optionen()
        if filt:
            auswahl[k] = {name: [w for w in form.getlist(f"f-{k}-{name}") if w in o["werte"]]
                          for name, o in filt.items()}
        themen[k] = len(b.themen_auswahl(auswahl.get(k)))
    try:
        rnd = int(form.get("zufall") or 42)
    except ValueError:
        rnd = 42
    vorschlag = "_".join(f"{k}{v}" for k, v in counts.items() if v) + f"_s{rnd}"
    name = str(form.get("name") or "").strip()
    ziel = paths.ausgabe_pfad("seeds", name or vorschlag, Ordner.output)
    return {"gewaehlt": gewaehlt, "counts": counts, "balance": balance, "auswahl": auswahl, "themen": themen,
            "zufall": rnd, "vorschlag": vorschlag, "ziel": ziel,
            "leer": [k for k in gewaehlt if not themen[k]]}


@app.get("/seeds", response_class=HTMLResponse)
def seeds_seite(request: Request):
    bereiche = []
    for k in BEREICHE:
        b = bereich(k)
        filt = b.auswahl_optionen()
        gruppen = []
        for g, titel in GRUPPEN:
            namen = [n for n, o in filt.items() if filtergruppe(n, o) == g]
            if namen:
                gruppen.append({"key": g, "titel": titel, "filter": [{
                    "name": n, **filt[n],
                    "standard": STANDARD_KONFIDENZ if g == "konfidenz" else filt[n]["standard"]} for n in namen]})
        bereiche.append({"key": k, "label": b.label, "balance": b.balance_options, "default_balance": b.default_balance,
                         "gruppen": gruppen, "themen_gesamt": len(b.topics()),
                         "themen": len(b.themen_auswahl(None))})
    return seite(request, "seite_seeds.html", bereiche=bereiche, status_werte=STATUS_WERTE,
                 standard_konfidenz=STANDARD_KONFIDENZ, status_text=STATUS_TEXT, lauf=lauf_ansicht("seeds"),
                 erster=next(iter(BEREICHE)))


_maximum: dict[str, tuple[int, int]] = {}


def umfang(key: str, auswahl: dict | None, anzahl_seeds: int, balance: str) -> dict:
    """Themen und Kombinationen der Auswahl gegenüber dem größtmöglichen Umfang (alle Filterwerte), dazu was die
    Filter ausschließen und wie oft eine Kombination bei anzahl_seeds Seeds im Schnitt vorkommt."""
    b = bereich(key)
    if key not in _maximum:
        alle = b.alle_auswahl()
        t = b.themen_auswahl(alle)
        _maximum[key] = (len(t), b.kombinationen(t, alle))
    gewaehlt = b.themen_auswahl(auswahl)
    komb = b.kombinationen(gewaehlt, auswahl)
    pruef = b.auswahl_pruefen(auswahl)
    aus = []
    for name, o in b.auswahl_optionen().items():
        if name == "belege":
            if pruef.get(name):
                aus.append((o["label"], [o["werte"][w] for w in pruef[name]], True))
            continue
        fehlt = [o["werte"][w] for w in o["werte"] if w not in pruef.get(name, [])]
        if fehlt:
            aus.append((o["label"], fehlt, False))
    t_max, k_max = _maximum[key]
    return {"themen": len(gewaehlt), "themen_max": t_max, "komb": komb, "komb_max": k_max,
            "label": b.kombination_label, "je_komb": anzahl_seeds / komb if komb else 0,
            "je_thema": anzahl_seeds / len(gewaehlt) if gewaehlt else 0, "ausgeschlossen": aus,
            "tipp_thema": balance != "thema" and "thema" in b.balance_options and 0 < anzahl_seeds < komb / 2}


@app.post("/seeds/pruefen", response_class=HTMLResponse)
async def seeds_pruefen(request: Request):
    f = seeds_formular(await request.form())
    vorhanden = dateien.zeilen(f["ziel"]) if f["ziel"].exists() else None
    T = uebersetzer(sprache(request))
    return seite(request, "teil_seeds_pruefen.html", f=f, vorhanden=vorhanden,
                 labels={k: bereich(k).label for k in BEREICHE},
                 umfaenge={k: umfang(k, f["auswahl"].get(k), f["counts"][k], f["balance"][k]) for k in f["gewaehlt"]},
                 leer_labels=", ".join(T(bereich(k).label) for k in f["leer"]))


@app.post("/seeds/erzeugen", response_class=HTMLResponse)
async def seeds_erzeugen(request: Request):
    f = seeds_formular(await request.form())
    T = uebersetzer(sprache(request))
    fehler = None
    if not sum(f["counts"].values()):
        fehler = T("Mindestens einen Bildungsbereich mit einer Anzahl über 0 wählen.")
    elif f["leer"]:
        fehler = T("Keine Themen ausgewählt für: {bereiche}. Filter erweitern.",
                   bereiche=", ".join(T(bereich(k).label) for k in f["leer"]))
    elif prozess.laeuft("seeds", Ordner.prozesse):
        fehler = T("Es werden gerade schon Seeds erzeugt.")
    if not fehler:
        argv = ["seeds", "--anzahl", *[f"{k}={v}" for k, v in f["counts"].items() if v], "--seed", str(f["zufall"]),
                "--out", str(f["ziel"]), "--gleichverteilung", *[f"{k}={v}" for k, v in f["balance"].items()]]
        auswahl = [f"{k}.{n}={','.join(w)}" for k, a in f["auswahl"].items() for n, w in a.items() if w]
        if auswahl:
            argv += ["--auswahl", *auswahl]
        starten("seeds", argv)
    return seite(request, "teil_status.html", lauf=lauf_ansicht("seeds"), name="seeds", fehler=fehler)


# --- Materialien -----------------------------------------------------------------------------------------------
@app.get("/materialien", response_class=HTMLResponse)
def materialien_seite(request: Request, aufgabe: str = "", lauf: str = ""):
    offen = unfertige()
    if aufgabe not in ("neu", "fortsetzen"):
        aufgabe = "fortsetzen" if lauf else "neu"
    e = einstellungen()
    ctx = {"aufgabe": aufgabe, "offen": offen, "e": e, "key": key_zustand(e), "mat": lauf_ansicht("mat"),
           "laeuft": prozess.laeuft("mat", Ordner.prozesse), "auswahl": offen.get(lauf),
           "seed_dateien": [(p, dateien.zeilen(p)) for p in ausgabedateien("seeds")], "sprachen": SPRACHEN}
    if aufgabe == "neu" and ctx["seed_dateien"]:
        ctx["neu"] = neu_felder_ctx(ctx["seed_dateien"][0][0].name)
    return seite(request, "seite_materialien.html", **ctx)


def neu_felder_ctx(src: str, name: str = "") -> dict:
    p = in_output(src, "seeds")
    vorschlag = p.stem.removeprefix(paths.PRAEFIX["seeds"])
    out = paths.ausgabe_pfad("materialien", name.strip() or vorschlag, Ordner.output)
    return {"src": p, "n_seeds": dateien.zeilen(p), "vorschlag": vorschlag, "name": name.strip() or vorschlag,
            "out": out, "fertig": dateien.zeilen(out) if out.exists() else 0}


@app.get("/materialien/neu-felder", response_class=HTMLResponse)
def materialien_neu_felder(request: Request, src: str, name: str = "", nur_hinweis: bool = False):
    ctx = neu_felder_ctx(src, name)
    return seite(request, "teil_mat_hinweis.html" if nur_hinweis else "teil_mat_neu_felder.html", neu=ctx)


def _mat_argv(src: Path, out: Path, sprache_: str, limit: int, lrt: bool) -> list[str]:
    return (["materialien", "--seeds", str(src), "--out", str(out), "--sprache", sprache_]
            + (["--limit", str(limit)] if limit else []) + ([] if lrt else ["--ohne-inhaltstyp-pruefung"]))


@app.post("/materialien/start", response_class=HTMLResponse)
def materialien_start(request: Request, src: str = Form(...), name: str = Form(""), limit: int = Form(0),
                      sprache_: str = Form("de", alias="sprache"), lrt: bool = Form(False),
                      verarbeitung: str = Form("sofort"), workers: int = Form(DEFAULT_WORKERS)):
    T = uebersetzer(sprache(request))
    ctx = neu_felder_ctx(src, name)
    if sprache_ not in SPRACHEN:
        sprache_ = "de"
    if prozess.laeuft("mat", Ordner.prozesse):
        return seite(request, "teil_status.html", lauf=lauf_ansicht("mat"), name="mat",
                     fehler=T("Es läuft schon ein Materiallauf. Erst anhalten oder abwarten."))
    if fremde_sperre(ctx["out"]):
        return seite(request, "teil_status.html", lauf=lauf_ansicht("mat"), name="mat",
                     fehler=T("An {datei} arbeitet gerade ein anderer Prozess (z. B. auf der Kommandozeile).",
                              datei=ctx["out"].name))
    cfg = llm_cfg(workers=max(1, min(1000, workers)), service_tier="flex" if verarbeitung == "flex" else None)
    argv = _mat_argv(ctx["src"], ctx["out"], sprache_, max(0, limit), lrt)
    if verarbeitung == "batch":
        argv.append("--batch")
    starten("mat", argv, cfg)
    return seite(request, "teil_status.html", lauf=lauf_ansicht("mat"), name="mat")


@app.get("/materialien/fortsetzen/{name}", response_class=HTMLResponse)
def fortsetzen_ansicht(request: Request, name: str):
    offen = unfertige()
    if name not in offen:
        return HTMLResponse('<p class="hinweis">' + _esc(uebersetzer(sprache(request))(
            "Dieser Lauf ist inzwischen fertig oder nicht mehr vorhanden.")) + "</p>")
    from ..laeufe import freier_name

    e = einstellungen()
    return seite(request, "teil_fortsetzen.html", L=offen[name], e=e, key=key_zustand(e),
                 laeuft=prozess.laeuft("mat", Ordner.prozesse), neu_name=freier_name(Path(offen[name]["ausgabe"])).name)


@app.post("/materialien/fortsetzen/{name}", response_class=HTMLResponse)
def fortsetzen(request: Request, name: str, aktion: str = Form("fortsetzen"), verarbeitung: str = Form("sofort"),
               workers: int = Form(DEFAULT_WORKERS)):
    from ..laeufe import freier_name

    T = uebersetzer(sprache(request))
    L = unfertige().get(name)
    if not L:
        raise HTTPException(404)
    if prozess.laeuft("mat", Ordner.prozesse) or L["fremd_pid"] or L["seeds_fehlt"]:
        return seite(request, "teil_status.html", lauf=lauf_ansicht("mat"), name="mat",
                     fehler=T("Es läuft schon ein Materiallauf. Erst anhalten oder abwarten."))
    batch = L["verarbeitung"] == "batch"
    cfg = llm_cfg(model=L["modell"], reasoning_effort=L["reasoning_effort"], workers=max(1, min(1000, workers)),
                  service_tier="flex" if verarbeitung == "flex" and not batch else None)
    if aktion == "neu":
        argv = _mat_argv(Path(L["seeds"]), freier_name(Path(L["ausgabe"])), L["ausgabesprache"], L["limit"] or 0,
                         L["inhaltstyp_pruefen"]) + (["--batch"] if batch else [])
    else:
        argv = ["materialien", "--fortsetzen", L["name"]]
        if not batch and verarbeitung != "flex":
            argv.append("--ohne-flex")
    starten("mat", argv, cfg)
    return seite(request, "teil_status.html", lauf=lauf_ansicht("mat"), name="mat")


@app.post("/materialien/sperre/{name}", response_class=HTMLResponse)
def sperre_aufheben(request: Request, name: str):
    from ..laeufe import sperre_aufheben as aufheben

    L = unfertige().get(name)
    if L and not L["fremd_pid"] and not prozess.laeuft("mat", Ordner.prozesse):
        aufheben(Path(L["ausgabe"]))
    return fortsetzen_ansicht(request, name)


# --- Vorschau --------------------------------------------------------------------------------------------------
_suchen_cache: dict = {}


@app.get("/vorschau", response_class=HTMLResponse)
def vorschau_seite(request: Request, art: str = "materialien", datei: str = "", nr: int = 1, q: str = "",
                   zufall: bool = False):
    art = art if art in paths.PRAEFIX else "materialien"
    liste = ausgabedateien(art)
    ctx: dict = {"art": art, "liste": [(p, dateien.zeilen(p)) for p in liste], "q": q, "datei": None}
    if liste:
        p = in_output(datei, art) if datei else liste[0]
        gesamt = dateien.zeilen(p)
        if q.strip():
            key = (str(p), gesamt, q.casefold().strip())
            if key not in _suchen_cache:
                _suchen_cache.clear()
                _suchen_cache[key] = dateien.suchen(p, q)
            treffer = _suchen_cache[key]
        else:
            treffer = None
        n = len(treffer) if treffer is not None else gesamt
        if zufall and n:
            nr = random.randint(1, n)
        nr = max(1, min(nr, n or 1))
        rec = None
        if n:
            rec = dateien.datensatz(p, treffer[nr - 1] if treffer is not None else nr - 1)
        ctx.update(datei=p, gesamt=gesamt, n=n, nr=nr, rec=rec, gekappt=treffer is not None and n >= 10_000,
                   ansicht=datensatz_ansicht(rec) if rec else None)
    return seite(request, "teil_vorschau.html" if teilansicht(request) else "seite_vorschau.html", **ctx)


def datensatz_ansicht(rec: dict) -> dict:
    th = rec.get(S.F_THEMA, {})
    return {"material": vorschau.ist_material(rec), "titel": rec.get(S.F_TITLE, ""),
            "beschreibung": rec.get(S.F_DESCRIPTION, ""), "keywords": rec.get(S.F_KEYWORD, []),
            "pruefung": rec.get(S.F_LRT_CHECK), "volltext": dateien.markdown(rec.get(S.F_FULLTEXT, "")),
            "meta": vorschau.metadaten(rec), "thema_text": th.get("beschreibung_de", ""),
            "thema_keywords": th.get("keywords_de", []), "qa": rec.get(S.F_QA) or [],
            "herkunft": json.dumps(rec.get(S.F_PROVENANCE, {}), ensure_ascii=False, indent=1),
            "roh": json.dumps(rec, ensure_ascii=False, indent=1), "id": rec.get("id", "")}


# --- Prompts ---------------------------------------------------------------------------------------------------
@app.get("/prompts", response_class=HTMLResponse)
def prompts_seite(request: Request, scope: str = "alle", which: str = "", gespeichert: bool = False):
    scope = scope if scope in BEREICHE else "alle"
    which = which if which in PROMPT_FILES else PROMPT_FILES[0]
    bkey = None if scope == "alle" else scope
    quelle = prompt_path(which, bkey)
    ziel = paths.PROMPT_DIR / f"{which}.md" if bkey is None else paths.PROMPT_DIR / bkey / f"{which}.md"
    text = quelle.read_text(encoding="utf-8")
    return seite(request, "seite_prompts.html", scope=scope, which=which, prompts=PROMPT_FILES,
                 bereiche={k: bereich(k).label for k in BEREICHE}, text=text,
                 quelle=quelle.relative_to(paths.ROOT).as_posix(), ziel=ziel.relative_to(paths.ROOT).as_posix(),
                 platzhalter=placeholders(text), gespeichert=gespeichert)


@app.post("/prompts")
def prompts_speichern(scope: str = Form("alle"), which: str = Form(...), text: str = Form(...)):
    if which not in PROMPT_FILES:
        raise HTTPException(400)
    bkey = scope if scope in BEREICHE else None
    ziel = paths.PROMPT_DIR / f"{which}.md" if bkey is None else paths.PROMPT_DIR / bkey / f"{which}.md"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text.replace("\r\n", "\n").strip() + "\n", encoding="utf-8")
    q = urlencode({"scope": scope, "which": which, "gespeichert": 1})
    return Response(status_code=204, headers={"HX-Redirect": f"/prompts?{q}"})


# --- Mappings --------------------------------------------------------------------------------------------------
@app.get("/mappings", response_class=HTMLResponse)
def mappings_seite(request: Request):
    gruppen = {"Vokabulare": sorted(paths.VOCAB_DIR.glob("*.json"))}
    for d in sorted(p for p in paths.MAPPING_DIR.iterdir() if p.is_dir()):
        titel = bereich(d.name).label if d.name in BEREICHE else d.name  # Ordner heißt wie der Bereichsschlüssel
        gruppen[titel] = sorted(p for p in d.iterdir() if p.suffix in (".json", ".jsonl", ".csv"))
    gruppen = {k: [(p.relative_to(paths.DATA_DIR).as_posix(), p.name, p.stat().st_size) for p in v]
               for k, v in gruppen.items()}
    bereiche = []
    for k in BEREICHE:
        b = bereich(k)
        schritte = [schritt_info(b, x) for x in b.mapping_schritte()]
        if not schritte:
            continue
        nach_key = {x["key"]: x for x in schritte}
        folgen = [(name, [nach_key[s] for s in keys if s in nach_key]) for name, keys in b.mapping_folgen.items()]
        in_folgen = {s for _, keys in b.mapping_folgen.items() for s in keys}
        rest = [x for x in schritte if x["key"] not in in_folgen]
        if rest:
            folgen.append(("weitere", rest))
        bereiche.append({"key": k, "label": b.label, "folgen": folgen, "lauf": lauf_ansicht(f"map-{k}"),
                         "wlo": any(x["wlo"] for x in schritte), "emb": any(x["emb"] for x in schritte)})
    e = einstellungen()
    return seite(request, "seite_mappings.html", gruppen=gruppen, bereiche=bereiche, e=e, key=key_zustand(e))


def schritt_info(b, x) -> dict:
    """Was ein Mapping-Schritt braucht. LLM steht am Schritt; WLO-Export, Embeddings und lokale Daten werden aus
    Beschreibung und Code des Schritts abgelesen (Hinweis für die Oberfläche, keine Garantie)."""
    import inspect

    try:
        code = inspect.getsource(x.run)
    except (OSError, TypeError):
        code = ""
    return {"key": x.key, "label": x.label, "beschreibung": x.beschreibung, "llm": x.llm,
            "wlo": "--wlo" in x.beschreibung or "wlo_dateien" in code, "emb": "embed" in code.lower(),
            "lokal": "rlp_ordner" in code, "abschluss": x.key in b.mapping_abschluss}


@app.get("/mappings/datei", response_class=HTMLResponse)
def mappings_datei(request: Request, pfad: str):
    p = (paths.DATA_DIR / pfad).resolve()
    if paths.DATA_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    inhalt = dateien.tabelle(p)
    if isinstance(inhalt, tuple):
        return seite(request, "teil_tabelle.html", spalten=inhalt[0], zeilen=inhalt[1], titel=p.name)
    return HTMLResponse(f'<pre class="json" tabindex="0">{_esc(json.dumps(inhalt, ensure_ascii=False, indent=1)[:200_000])}</pre>')


@app.get("/mappings/statistik/{key}", response_class=HTMLResponse)
def mappings_statistik(key: str):
    return HTMLResponse(f'<pre class="json" tabindex="0">'
                        f'{_esc(json.dumps(bereich(key).mapping_statistik(), ensure_ascii=False, indent=1))}</pre>')


@app.post("/mappings/{key}/start", response_class=HTMLResponse)
async def mappings_start(request: Request, key: str):
    form = await request.form()
    b = bereich(key)
    T = uebersetzer(sprache(request))
    erlaubt = [s.key for s in b.mapping_schritte()]
    wahl = [k for k in erlaubt if k in form.getlist("schritte")]
    job = f"map-{key}"
    fehler = None
    if not wahl:
        fehler = T("Mindestens einen Schritt wählen.")
    elif prozess.laeuft(job, Ordner.prozesse):
        fehler = T("Für diesen Bereich läuft schon ein Mapping.")
    else:
        def ganz(name, std, lo, hi):
            try:
                return max(lo, min(hi, int(form.get(name) or std)))
            except ValueError:
                return std
        lim, stimmen = ganz("limit", 0, 0, 100_000), ganz("stimmen", 3, 1, 7)
        wlo = [x.strip() for x in str(form.get("wlo") or "").split(",") if x.strip()]
        e = einstellungen()
        emb = str(form.get("embedding_modell") or "").strip() or e.embedding_modell
        argv = ["mappings", key, *wahl, "--stimmen", str(stimmen), "--embedding-modell", emb] \
            + (["--limit", str(lim)] if lim else []) + (["--wlo", *wlo] if wlo else [])
        starten(job, argv, llm_cfg(e))
    return seite(request, "teil_status.html", lauf=lauf_ansicht(job), name=job, fehler=fehler)


# --- Methodik --------------------------------------------------------------------------------------------------
@app.get("/methodik", response_class=HTMLResponse)
def methodik_seite(request: Request):
    p = paths.ROOT / ("METHODIK.md" if sprache(request) == "de" else "METHODOLOGY.md")
    text = (p if p.exists() else paths.ROOT / "METHODIK.md").read_text(encoding="utf-8")
    text = "\n".join(z for z in text.splitlines() if not z.startswith(("*English ·", "*[English]")))
    return seite(request, "seite_methodik.html", html=dateien.markdown(text, doku=True))


def url(pfad: str, **params) -> str:
    return pfad + ("?" + urlencode({k: v for k, v in params.items() if v not in (None, "")}) if params else "")


vorlagen.env.globals.update(url=url, quote=quote, gruppe_text=dict(GRUPPEN))
