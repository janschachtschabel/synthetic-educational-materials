"""Einfache Oberfläche: streamlit run app.py"""

from __future__ import annotations

import json
import threading

import pandas as pd
import streamlit as st

from sem import schema as S
from sem import seeds as seedlib
from sem import vorschau
from sem.bereiche import BEREICHE
from sem.generate import generate
from sem.llm import DEFAULT_MODEL, DEFAULT_WORKERS, LLMConfig, Progress
from sem.mapping.embeddings import DEFAULT_EMBEDDING_MODEL
from sem.paths import MAPPING_DIR, OUTPUT_DIR, PROMPT_DIR, ROOT, VOCAB_DIR
from sem.prompts import PROMPT_FILES, placeholders, prompt_path

st.set_page_config(page_title="Synthetische Bildungsmaterialien", layout="wide")
OUTPUT_DIR.mkdir(exist_ok=True)


@st.cache_resource
def bereich(key):
    return BEREICHE[key]()


def jobs() -> dict:
    """Laufende Hintergrundjobs, über Reloads hinweg."""
    return st.session_state.setdefault("jobs", {})


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

# --- Vorschau-Helfer --------------------------------------------------------------------------
@st.cache_data(max_entries=64)
def _dateiart(path: str, mtime: float) -> str:
    return vorschau.dateiart(path)


def ausgabedateien(art: str) -> list:
    """Dateien in output/ einer Art („seeds“ oder „materialien“), neueste zuerst."""
    ps = [p for p in OUTPUT_DIR.glob("*.jsonl") if ".fehler" not in p.name]
    return sorted((p for p in ps if _dateiart(str(p), p.stat().st_mtime) == art),
                  key=lambda p: p.stat().st_mtime, reverse=True)


@st.cache_data(max_entries=4)
def _lade(path: str, mtime: float) -> list[dict]:
    return vorschau.lade(path)


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
        st.info("Seed (noch kein Material erzeugt)")
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
    st.caption("Ohne LLM. Gleicher Zufalls-Seed und gleiche Einstellungen ergeben dieselben Seeds.")
    counts, balance, auswahl = {}, {}, {}
    for key in BEREICHE:
        b = bereich(key)
        c1, c2 = st.columns([1, 2])
        counts[key] = c1.number_input(f"{b.label}: Anzahl", 0, 1_000_000, 1000, step=500, key=f"n-{key}")
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
    rnd = st.number_input("Zufalls-Seed", 0, 2**31 - 1, 42)
    name = st.text_input("Dateiname", value=f"seeds_s{rnd}.jsonl")
    if st.button("Seeds erzeugen", type="primary"):
        with st.spinner("erzeuge …"):
            data = seedlib.build(counts, balance, int(rnd), auswahl)
            stats = seedlib.write(data, OUTPUT_DIR / name, {"anzahl": counts, "gleichverteilung": balance,
                                                            "zufalls_seed": int(rnd), "auswahl": auswahl})
        st.success(f"{stats['anzahl']} Seeds → output/{name}")
        st.json(stats, expanded=False)
        st.download_button("Herunterladen", (OUTPUT_DIR / name).read_bytes(), file_name=name)

# --- Materialien ----------------------------------------------------------------------------
with tab_mat:
    st.subheader("Materialien erzeugen")
    st.caption("Pro Seed: Volltext, dann je ein Aufruf für Titel, Beschreibung, Keywords, Inhaltstyp-Prüfung und "
               "Frage-Antwort-Paare. Ein erneuter Start setzt fort und überspringt Fertiges.")
    files = [p.name for p in ausgabedateien("seeds")]
    if not files:
        st.info("Erst Seeds erzeugen.")
    else:
        src = st.selectbox("Seed-Datei", files)
        n_seeds = sum(1 for _ in (OUTPUT_DIR / src).open(encoding="utf-8"))
        limit = st.number_input(f"Anzahl (von {n_seeds} Seeds, 0 = alle)", 0, n_seeds, 0)
        out = st.text_input("Ausgabedatei", value="materialien_" + src.removeprefix("seeds_"))
        lrt_check = st.checkbox("Inhaltstyp am fertigen Material prüfen", value=True,
                                help="Bestimmt den Inhaltstyp ohne Kenntnis des Seeds und protokolliert, ob er passt.")
        if st.button("Start / Fortsetzen", type="primary", disabled=bool(jobs().get("mat") and not jobs()["mat"]["done"])):
            start_job("mat", generate, OUTPUT_DIR / src, OUTPUT_DIR / out, cfg, limit=int(limit) or None,
                      inhaltstyp_pruefen=lrt_check)
        job_status("mat")
        if (OUTPUT_DIR / out).exists():
            letzte = vorschau.lade(OUTPUT_DIR / out)[-1:]
            if letzte:
                if st.toggle("Zuletzt erzeugtes Material zeigen (alle im Reiter „Vorschau“)", key="mat-letzte"):
                    with st.container(border=True):
                        zeige(letzte[0])
        if (OUTPUT_DIR / out).exists():
            st.download_button("Ergebnis herunterladen", (OUTPUT_DIR / out).read_bytes(), file_name=out)

# --- Vorschau ---------------------------------------------------------------------------------
with tab_vorschau:
    st.subheader("Vorschau")
    art = st.radio("Anzeigen", ["materialien", "seeds"], horizontal=True, key="v-art",
                   format_func=lambda a: {"materialien": "Erzeugte Materialien", "seeds": "Seeds"}[a])
    dateien = ausgabedateien(art)
    if not dateien:
        st.info("Noch keine erzeugten Materialien in output/ – erst im Reiter „Materialien“ starten."
                if art == "materialien" else "Noch keine Seeds in output/.")
    else:
        datei = st.selectbox("Datei (neueste zuerst)", dateien, format_func=lambda p: p.name, key=f"v-datei-{art}")
        rows = _lade(str(datei), datei.stat().st_mtime)
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
    st.caption("Platzhalter: " + (", ".join(placeholders(text)) or "keine"))
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
                if p.suffix == ".csv":
                    st.dataframe(pd.read_csv(p), height=250)
                elif p.suffix == ".jsonl":
                    rows = [json.loads(l) for _, l in zip(range(200), p.open(encoding="utf-8"))]
                    st.dataframe(pd.json_normalize(rows), height=250)
                else:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    items = data.get("concepts") or data.get("inhaltstypen") or data.get("niveaus")
                    st.dataframe(pd.DataFrame(items)) if items else st.json(data, expanded=False)
                st.download_button("Herunterladen", p.read_bytes(), file_name=p.name, key=f"dl-{p}")

    st.markdown("**Mappings neu bauen** (fortsetzbar; bereits Erledigtes wird übersprungen)")
    for key in BEREICHE:
        b = bereich(key)
        schritte = {x.key: x for x in b.mapping_schritte()}
        if not schritte:
            continue
        with st.container(border=True):
            st.markdown(f"**{b.label}**")
            stat = b.mapping_statistik()
            if stat:
                with st.expander("Aktueller Stand des Mappings"):
                    st.json(stat)
            wahl = st.multiselect("Schritte", list(schritte), default=b.mapping_folge, key=f"ms-{key}",
                                  format_func=lambda k, s=schritte: f"{s[k].label}{' (LLM)' if s[k].llm else ''}")
            c1, c2 = st.columns(2)
            lim = c1.number_input("Nur die ersten N Themen (0 = alle)", 0, 100_000, 0, key=f"ml-{key}")
            stimmen = c2.number_input("Stimmen je Zuordnung", 1, 7, 3, key=f"mv-{key}")
            wlo_txt = st.text_input("WLO-Exporte (CSV-Pfade, durch Komma getrennt; bleiben lokal)", key=f"mw-{key}")
            job = f"map-{key}"
            laeuft = bool(jobs().get(job) and not jobs()[job]["done"])
            if st.button("Start / Fortsetzen", key=f"mb-{key}", disabled=laeuft or not wahl):
                start_job(job, b.mapping_ausfuehren, [k for k in schritte if k in wahl], cfg,
                          limit=int(lim) or None, stimmen=int(stimmen), embedding_modell=emb_model,
                          wlo_dateien=[x.strip() for x in wlo_txt.split(",") if x.strip()] or None)
            job_status(job)

# --- Methodik -------------------------------------------------------------------------------
with tab_meth:
    st.markdown((ROOT / "METHODIK.md").read_text(encoding="utf-8"))
