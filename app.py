"""Einfache Oberfläche: streamlit run app.py"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

from sem import schema as S
from sem import seeds as seedlib
from sem import vorschau
from sem.bereiche import BEREICHE
from sem.generate import generate
from sem.llm import DEFAULT_MODEL, DEFAULT_WORKERS, LLMConfig, Progress
from sem.mapping.embeddings import DEFAULT_EMBEDDING_MODEL
from sem.paths import (MAPPING_DIR, OUTPUT_DIR, PRAEFIX, PROMPT_DIR, ROOT, VOCAB_DIR, ausgabe_pfad,
                       ausgabedateien)
from sem.prompts import PROMPT_FILES, placeholders, prompt_path

st.set_page_config(page_title="Synthetische Bildungsmaterialien", layout="wide")
OUTPUT_DIR.mkdir(exist_ok=True)


@st.cache_resource
def bereich(key):
    return BEREICHE[key]()


@st.cache_resource
def jobs() -> dict:
    """Laufende Hintergrundjobs, app-weit: bleiben nach Neuladen oder in einem neuen Tab sichtbar."""
    return {}


def start_job(name: str, fn, *args, **kwargs):
    stop = threading.Event()
    job = {"progress": None, "stop": stop, "error": None, "done": False}

    def on_progress(p: Progress):
        job["progress"] = p

    def run():
        try:
            job["progress"] = fn(*args, on_progress=on_progress, stop_event=stop, **kwargs)
        except Exception as e:  # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
        job["done"] = True

    threading.Thread(target=run, daemon=True).start()
    jobs()[name] = job


@st.fragment(run_every=2)
def job_status(name: str):
    job = jobs().get(name)
    if not job:
        return
    p = job["progress"]
    if p and p.total:
        st.progress(min(1.0, (p.done + p.failed) / p.total), text=p.line())
    elif p:
        st.info(p.line())
    else:
        st.info("startet …")
    if job["error"]:
        st.error(job["error"])
    if not job["done"] and st.button("Anhalten", key=f"stop-{name}"):
        job["stop"].set()


# --- Seitenleiste: LLM ---------------------------------------------------------------------
with st.sidebar:
    st.header("LLM-Anbindung")
    st.caption("OpenAI-kompatibel. Leere Felder: Werte aus OPENAI_BASE_URL, OPENAI_API_KEY, LLM_MODEL.")
    env = LLMConfig.from_env()
    base_url = st.text_input("Base-URL", value=env.base_url or "", placeholder="https://api.openai.com/v1")
    model = st.text_input("Modell", value=env.model or DEFAULT_MODEL)
    api_key = st.text_input("API-Key", type="password", placeholder="aus OPENAI_API_KEY" if env.api_key else "")
    st.header("Geschwindigkeit")
    workers = st.number_input("Worker (parallel)", 1, 1000, env.workers or DEFAULT_WORKERS,
                              help="Materialien, die gleichzeitig erzeugt werden. Mehr Worker = schneller, "
                                   "solange das Rate-Limit des Anbieters reicht.")
    efforts = ["minimal", "low", "medium", "high", "Modellstandard"]
    effort = st.radio("Reasoning-Aufwand", efforts, horizontal=True,
                      index=efforts.index(env.reasoning_effort or "Modellstandard"),
                      help="Nur für Reasoning-Modelle. low ist Standard: gemessen ein Drittel schneller als "
                           "der Modellstandard bei gleichen Prüfwerten. „Modellstandard“ sendet den Parameter "
                           "nicht (für Modelle, die ihn nicht kennen).")
    retries = st.number_input("Versuche pro Aufgabe", 1, 20, 6)
    outage = st.number_input("Wartezeit bei API-Ausfall (Minuten)", 1, 240, 10,
                             help="Danach pausiert der Lauf und lässt sich später fortsetzen.")
    st.header("Embeddings")
    emb_model = st.text_input("Embedding-Modell (lokal, CPU)", value=DEFAULT_EMBEDDING_MODEL,
                              help="z. B. intfloat/multilingual-e5-small oder "
                                   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    cfg = LLMConfig(base_url=base_url or None, api_key=api_key or env.api_key, model=model, workers=int(workers),
                    max_retries=int(retries), outage_minutes=float(outage),
                    reasoning_effort=None if effort == "Modellstandard" else effort)

# --- Helfer für Dateien in output/ ------------------------------------------------------------
@st.cache_data(max_entries=64)
def _zeilen(path: str, mtime: float) -> int:
    with open(path, encoding="utf-8") as f:
        return sum(1 for l in f if l.strip())


def anzahl(n: int, eins: str, viele: str) -> str:
    return f"{n} {eins if n == 1 else viele}"


def zeilen(p) -> int:
    return _zeilen(str(p), p.stat().st_mtime) if p.exists() else 0


def letzter_datensatz(p) -> dict | None:
    """Letzte Zeile einer JSONL-Datei, ohne die ganze Datei zu lesen."""
    with open(p, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        f.seek(max(0, pos - 2_000_000))
        lines = [l for l in f.read().splitlines() if l.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        return None


def dateiname_feld(label: str, art: str, vorschlag: str, key: str, hilfe: str):
    """Eingabe nur für den freien Teil des Namens; Präfix und Endung sind fest."""
    c1, c2, c3 = st.columns([2, 5, 1])
    c1.text_input(label, PRAEFIX[art], disabled=True, key=f"{key}-praefix",
                  help="Feste Namensanfänge: seeds_ für Seed-Dateien, materialien_ für erzeugte Materialien. "
                       "Daran erkennt die App, welche Dateien in welche Auswahl gehören.")
    frei = c2.text_input("Name", vorschlag, key=key, help=hilfe)
    c3.text_input("Endung", ".jsonl", disabled=True, key=f"{key}-endung")
    return ausgabe_pfad(art, frei)


def download(p, label: str, key: str):
    """Download-Knopf; die Datei wird erst beim Klick gelesen."""
    groesse = p.stat().st_size
    st.download_button(f"{label} ({groesse / 1024 ** 2:.1f} MB)", lambda: Path(p).read_bytes(), file_name=p.name,
                       key=key, on_click="ignore")


@st.cache_data(max_entries=4)
def _lade(path: str, mtime: float, limit: int | None) -> list[dict]:
    return vorschau.lade(path, limit)


def zeige(rec: dict):
    """Ein Seed oder Material: erzeugte Inhalte, Metadaten, Herkunft."""
    if vorschau.ist_material(rec):
        st.markdown(f"### {rec.get(S.F_TITLE, '')}")
        st.write(rec.get(S.F_DESCRIPTION, ""))
        st.markdown(" ".join(f"`{k}`" for k in rec.get(S.F_KEYWORD, [])))
        chk = rec.get(S.F_LRT_CHECK)
        if chk:
            text = f"Inhaltstyp-Prüfung: erkannt „{chk['erkannt_label']}“"
            (st.success if chk["uebereinstimmung"] else st.warning)(
                text + (" – passt zum Seed" if chk["uebereinstimmung"] else " – weicht vom Seed ab"))
    else:
        st.info("Das ist ein Seed: die Vorgaben für ein Material. Erzeugte Materialien zeigt der Umschalter "
                "„Erzeugte Materialien“.")
    c1, c2 = st.columns([3, 2]) if vorschau.ist_material(rec) else (None, st.container())
    if c1 is not None:
        with c1:
            st.markdown("**Volltext**")
            with st.container(border=True, height=520):
                st.markdown(rec[S.F_FULLTEXT])
    with c2:
        st.markdown("**Metadaten aus dem Seed**")
        st.table(pd.DataFrame(vorschau.metadaten(rec).items(), columns=["Feld", "Wert"]).set_index("Feld"))
        t = rec.get(S.F_THEMA, {})
        if t.get("beschreibung_de") or t.get("keywords_de"):
            with st.expander("Thema aus dem Seed"):
                st.write(t.get("beschreibung_de", ""))
                st.caption(", ".join(t.get("keywords_de", [])))
    if rec.get(S.F_QA):
        st.markdown("**Frage-Antwort-Paare**")
        st.dataframe(pd.DataFrame(rec[S.F_QA]), hide_index=True, width="stretch")
    with st.expander("Herkunft je Feld"):
        st.json(rec.get(S.F_PROVENANCE, {}), expanded=False)
    with st.expander("Rohdaten (JSON)"):
        st.json(rec, expanded=False)


tab_seeds, tab_mat, tab_vorschau, tab_prompts, tab_map, tab_meth = st.tabs(
    ["Seeds", "Materialien", "Vorschau", "Prompts", "Mappings", "Methodik"])

# --- Seeds ----------------------------------------------------------------------------------
with tab_seeds:
    st.subheader("Seeds erzeugen")
    st.caption("Ohne LLM. Gleicher Startwert für den Zufall und gleiche Einstellungen ergeben dieselben Seeds.")
    counts, balance, auswahl = {}, {}, {}
    for i, key in enumerate(BEREICHE):
        b = bereich(key)
        c1, c2 = st.columns([1, 2])
        counts[key] = c1.number_input(f"{b.label}: Anzahl", 0, 1_000_000, 1000 if i == 0 else 0, step=500,
                                      key=f"n-{key}")
        opts = list(b.balance_options)
        balance[key] = c2.selectbox(f"{b.label}: gleich verteilen nach", opts, index=opts.index(b.default_balance),
                                    format_func=lambda o, b=b: f"{o} – {b.balance_options[o]}", key=f"bal-{key}")
        filt = b.auswahl_optionen()
        if filt:
            with st.expander(f"{b.label}: welche Themen einbezogen werden (Status, Quellen, Belege)"):
                wahl = {}
                for name, o in filt.items():
                    wahl[name] = st.multiselect(o["label"], list(o["werte"]), default=o["standard"],
                                                key=f"f-{key}-{name}", help=o["beschreibung"],
                                                format_func=lambda w, o=o: f"{w} – {o['werte'][w]}")
                auswahl[key] = wahl
                st.caption(f"{len(b.themen_auswahl(wahl))} von {len(b.topics())} Themen ausgewählt. "
                           "Voreinstellung: gesichert, wahrscheinlich und redaktionell; unsicher erst nach Prüfung.")
    rnd = st.number_input("Startwert für den Zufall", 0, 2**31 - 1, 42,
                          help="Gleicher Startwert und gleiche Einstellungen ergeben dieselben Seeds.")
    ziel = dateiname_feld("Dateiname", "seeds", "_".join(f"{k}{v}" for k, v in counts.items() if v) + f"_s{rnd}",
                          "s-name",
                          "Frei wählbar, z. B. hochschule30000_s42")
    vorhanden = ziel.exists()
    if vorhanden:
        st.warning(f"{ziel.name} gibt es schon ({anzahl(zeilen(ziel), 'Seed', 'Seeds')}) und wird beim Erzeugen "
                   "überschrieben.")
    if st.button("Seeds erzeugen", type="primary", disabled=sum(counts.values()) == 0):
        with st.spinner("erzeuge …"):
            data = seedlib.build(counts, balance, int(rnd), auswahl)
            stats = seedlib.write(data, ziel, {"anzahl": counts, "gleichverteilung": balance,
                                               "zufalls_seed": int(rnd), "auswahl": auswahl})
        st.success(f"{stats['anzahl']} Seeds in output/{ziel.name}. Weiter im Reiter „Materialien“.")
        with st.expander("Statistik"):
            st.json(stats, expanded=False)
        download(ziel, "Seeds herunterladen", "dl-seeds")

# --- Materialien ----------------------------------------------------------------------------
with tab_mat:
    st.subheader("Materialien erzeugen")
    st.caption("Pro Seed: Volltext, dann je ein Aufruf für Titel, Beschreibung, Keywords, Inhaltstyp-Prüfung und "
               "Frage-Antwort-Paare. Ein erneuter Start setzt fort und überspringt Fertiges.")
    seed_dateien = ausgabedateien("seeds")
    if not seed_dateien:
        st.info("Noch keine Seed-Dateien (seeds_…) in output/. Erst im Reiter „Seeds“ erzeugen.")
    else:
        src = st.selectbox("Seed-Datei (neueste zuerst)", seed_dateien, key="m-src",
                           format_func=lambda p: f"{p.name} – {anzahl(zeilen(p), 'Seed', 'Seeds')}",
                           help="Hier stehen nur Dateien, die mit seeds_ beginnen.")
        n_seeds = zeilen(src)
        limit = st.number_input(f"Wie viele Seeds verarbeiten? (0 = alle {n_seeds})", 0, n_seeds, 0,
                                help="Es werden die ersten N Seeds der Datei genommen.")
        out = dateiname_feld("Ausgabedatei", "materialien", src.stem.removeprefix(PRAEFIX["seeds"]),
                             f"m-out-{src.name}", "Standard: derselbe Name wie die Seed-Datei")
        fertig_n = zeilen(out)
        if fertig_n:
            st.info(f"{out.name} enthält schon {anzahl(fertig_n, 'Material', 'Materialien')}. Start setzt fort und überspringt Fertiges. "
                    "Für einen neuen Lauf einen anderen Namen wählen.")
        lrt_check = st.checkbox("Inhaltstyp am fertigen Material prüfen", value=True,
                                help="Bestimmt den Inhaltstyp ohne Kenntnis des Seeds und protokolliert, ob er "
                                     "passt. Ein Aufruf mehr je Material.")
        laeuft = bool(jobs().get("mat") and not jobs()["mat"]["done"])
        st.caption(f"Einstellungen aus der Seitenleiste: {cfg.model}, {cfg.workers} Worker, "
                   f"Reasoning-Aufwand {cfg.reasoning_effort or 'Modellstandard'}.")
        if st.button("Start / Fortsetzen", type="primary", disabled=laeuft):
            start_job("mat", generate, src, out, cfg, limit=int(limit) or None, inhaltstyp_pruefen=lrt_check)
        job_status("mat")
        if out.exists() and not laeuft:
            letzte = letzter_datensatz(out)
            if letzte and st.toggle("Zuletzt erzeugtes Material zeigen (alle im Reiter „Vorschau“)", key="mat-letzte"):
                with st.container(border=True):
                    zeige(letzte)
            download(out, "Materialien herunterladen", "dl-mat")

# --- Vorschau ---------------------------------------------------------------------------------
with tab_vorschau:
    st.subheader("Vorschau")
    art = st.radio("Anzeigen", ["materialien", "seeds"], horizontal=True, key="v-art",
                   format_func=lambda a: {"materialien": "Erzeugte Materialien (materialien_…)",
                                          "seeds": "Seeds (seeds_…)"}[a])
    dateien = ausgabedateien(art)
    if not dateien:
        st.info("Noch keine erzeugten Materialien in output/ – erst im Reiter „Materialien“ starten."
                if art == "materialien" else "Noch keine Seeds in output/.")
    else:
        einheit = ("Material", "Materialien") if art == "materialien" else ("Seed", "Seeds")
        datei = st.selectbox("Datei (neueste zuerst)", dateien, key=f"v-datei-{art}",
                             format_func=lambda p: f"{p.name} – {anzahl(zeilen(p), *einheit)}")
        gesamt = zeilen(datei)
        limit = None
        if gesamt > 5000:
            limit = st.number_input(f"Datensätze laden (von {gesamt}; große Dateien brauchen Zeit)", 100, gesamt,
                                    5000, step=1000, key=f"v-limit-{art}",
                                    help="Suche und Blättern beziehen sich auf die geladenen Datensätze.")
        rows = _lade(str(datei), datei.stat().st_mtime, limit)
        c1, c2 = st.columns([2, 1])
        such = c1.text_input("Suchen (Thema, Fach, Inhaltstyp, Titel, Keyword, id)", key="v-such").casefold()
        treffer = [r for r in rows if such in vorschau.suchtext(r)] if such else rows
        c2.metric("Datensätze", f"{len(treffer)} / {len(rows)}")
        if treffer:
            if st.session_state.get("v-nr", 1) > len(treffer):
                st.session_state["v-nr"] = 1
            b1, b2 = st.columns([1, 4])
            if b1.button("Zufällig", key="v-zufall"):
                import random

                st.session_state["v-nr"] = random.randint(1, len(treffer))
            nr = b2.number_input("Nr.", 1, len(treffer), key="v-nr")
            zeige(treffer[int(nr) - 1])

# --- Prompts --------------------------------------------------------------------------------
with tab_prompts:
    st.subheader("Prompts bearbeiten")
    scope = st.selectbox("Gültig für", ["alle Bereiche"] + list(BEREICHE))
    which = st.selectbox("Prompt", PROMPT_FILES)
    bkey = None if scope == "alle Bereiche" else scope
    path = prompt_path(which, bkey)
    target = PROMPT_DIR / f"{which}.md" if bkey is None else PROMPT_DIR / bkey / f"{which}.md"
    st.caption(f"Quelle: {path.relative_to(ROOT)} · speichert nach: {target.relative_to(ROOT)}")
    text = st.text_area("Text", path.read_text(encoding="utf-8"), height=320, key=f"p-{scope}-{which}")
    st.caption("Platzhalter: " + (", ".join(placeholders(text)) or "keine") + " · Jedes Material speichert den "
               "Hash der verwendeten Prompts, Änderungen bleiben so nachvollziehbar.")
    if st.button("Speichern"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.strip() + "\n", encoding="utf-8")
        st.success("gespeichert – gilt für den nächsten Start")

# --- Mappings -------------------------------------------------------------------------------
with tab_map:
    st.subheader("Vokabulare und Mappings")
    st.caption("Alle Dateien sind auch einzeln nutzbar. Beschreibung der Felder: data/README.md")
    groups = {"Vokabulare": sorted(VOCAB_DIR.glob("*.json"))}
    for d in sorted(p for p in MAPPING_DIR.iterdir() if p.is_dir()):
        groups[f"Mappings {d.name}"] = sorted(p for p in d.iterdir() if p.suffix in (".json", ".jsonl", ".csv"))
    for title, paths in groups.items():
        st.markdown(f"**{title}**")
        for p in paths:
            with st.expander(f"{p.name} ({p.stat().st_size // 1024} KB)"):
                download(p, "Herunterladen", f"dl-{p}")
                if not st.toggle("Inhalt anzeigen", key=f"show-{p}"):
                    continue
                if p.suffix == ".csv":
                    st.dataframe(pd.read_csv(p), height=250)
                elif p.suffix == ".jsonl":
                    rows = [json.loads(l) for _, l in zip(range(200), p.open(encoding="utf-8"))]
                    st.dataframe(pd.json_normalize(rows), height=250)
                else:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    items = data.get("concepts") or data.get("inhaltstypen") or data.get("niveaus")
                    st.dataframe(pd.DataFrame(items)) if items else st.json(data, expanded=False)

    st.markdown("**Mappings neu bauen** (fortsetzbar; bereits Erledigtes wird übersprungen)")
    for key in BEREICHE:
        b = bereich(key)
        schritte = {x.key: x for x in b.mapping_schritte()}
        if not schritte:
            continue
        with st.container(border=True):
            st.markdown(f"**{b.label}**")
            if st.toggle("Aktuellen Stand des Mappings anzeigen", key=f"stat-{key}"):
                st.json(b.mapping_statistik())
            wahl = st.multiselect("Schritte", list(schritte), default=[], key=f"ms-{key}",
                                  placeholder="Schritte wählen (die Daten im Repo sind fertig gebaut)",
                                  format_func=lambda k, s=schritte: f"{s[k].label}{' (LLM)' if s[k].llm else ''}")
            if any(schritte[k].llm for k in wahl):
                st.warning("Ausgewählte Schritte rufen das LLM auf und verursachen Kosten. Bereits Erledigtes "
                           "wird übersprungen.")
            c1, c2 = st.columns(2)
            lim = c1.number_input("Nur die ersten N Themen (0 = alle)", 0, 100_000, 0, key=f"ml-{key}")
            stimmen = c2.number_input("Stimmen je Zuordnung", 1, 7, 3, key=f"mv-{key}")
            wlo_txt = st.text_input("WLO-Exporte (CSV-Pfade, durch Komma getrennt; bleiben lokal)", key=f"mw-{key}")
            job = f"map-{key}"
            laeuft = bool(jobs().get(job) and not jobs()[job]["done"])
            if st.button("Start / Fortsetzen", key=f"mb-{key}", disabled=laeuft or not wahl):
                start_job(job, b.mapping_ausfuehren, b.schritte_aufloesen([k for k in schritte if k in wahl]), cfg,
                          limit=int(lim) or None, stimmen=int(stimmen), embedding_modell=emb_model,
                          wlo_dateien=[x.strip() for x in wlo_txt.split(",") if x.strip()] or None)
            job_status(job)

# --- Methodik -------------------------------------------------------------------------------
with tab_meth:
    st.markdown((ROOT / "METHODIK.md").read_text(encoding="utf-8"))
