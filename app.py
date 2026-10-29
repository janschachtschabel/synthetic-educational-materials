"""Einfache Oberfläche: streamlit run app.py"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

from sem import seeds as seedlib
from sem.bereiche import BEREICHE
from sem.generate import generate
from sem.llm import DEFAULT_MODEL, DEFAULT_WORKERS, LLMConfig, Progress
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
    workers = st.number_input("Worker (parallel)", 1, 200, env.workers or DEFAULT_WORKERS)
    retries = st.number_input("Versuche pro Aufgabe", 1, 20, 6)
    outage = st.number_input("Wartezeit bei API-Ausfall (Minuten)", 1, 240, 10,
                             help="Danach pausiert der Lauf und lässt sich später fortsetzen.")
    cfg = LLMConfig(base_url=base_url or None, api_key=api_key or env.api_key, model=model, workers=int(workers),
                    max_retries=int(retries), outage_minutes=float(outage))

tab_seeds, tab_mat, tab_prompts, tab_map, tab_meth = st.tabs(
    ["Seeds", "Materialien", "Prompts", "Mappings", "Methodik"])

# --- Seeds ----------------------------------------------------------------------------------
with tab_seeds:
    st.subheader("Seeds erzeugen")
    st.caption("Ohne LLM. Gleicher Zufalls-Seed und gleiche Einstellungen ergeben dieselben Seeds.")
    counts, balance = {}, {}
    for key in BEREICHE:
        b = bereich(key)
        c1, c2 = st.columns([1, 2])
        counts[key] = c1.number_input(f"{b.label}: Anzahl", 0, 1_000_000, 1000, step=500, key=f"n-{key}")
        opts = list(b.balance_options)
        balance[key] = c2.selectbox(f"{b.label}: gleich verteilen nach", opts, index=opts.index(b.default_balance),
                                    format_func=lambda o, b=b: f"{o} – {b.balance_options[o]}", key=f"bal-{key}")
    rnd = st.number_input("Zufalls-Seed", 0, 2**31 - 1, 42)
    name = st.text_input("Dateiname", value=f"seeds_s{rnd}.jsonl")
    if st.button("Seeds erzeugen", type="primary"):
        with st.spinner("erzeuge …"):
            data = seedlib.build(counts, balance, int(rnd))
            stats = seedlib.write(data, OUTPUT_DIR / name,
                                  {"anzahl": counts, "gleichverteilung": balance, "zufalls_seed": int(rnd)})
        st.success(f"{stats['anzahl']} Seeds → output/{name}")
        st.json(stats, expanded=False)
        st.download_button("Herunterladen", (OUTPUT_DIR / name).read_bytes(), file_name=name)

# --- Materialien ----------------------------------------------------------------------------
with tab_mat:
    st.subheader("Materialien erzeugen")
    st.caption("Pro Seed: Volltext, dann Titel, Beschreibung, Keywords und Frage-Antwort-Paare aus dem Volltext. "
               "Ein erneuter Start setzt fort und überspringt Fertiges.")
    files = sorted(p.name for p in OUTPUT_DIR.glob("*.jsonl")
                   if not p.name.startswith("materialien") and ".fehler" not in p.name)
    if not files:
        st.info("Erst Seeds erzeugen.")
    else:
        src = st.selectbox("Seed-Datei", files)
        n_seeds = sum(1 for _ in (OUTPUT_DIR / src).open(encoding="utf-8"))
        limit = st.number_input(f"Anzahl (von {n_seeds} Seeds, 0 = alle)", 0, n_seeds, 0)
        out = st.text_input("Ausgabedatei", value="materialien_" + src.removeprefix("seeds_"))
        if st.button("Start / Fortsetzen", type="primary", disabled=bool(jobs().get("mat") and not jobs()["mat"]["done"])):
            start_job("mat", generate, OUTPUT_DIR / src, OUTPUT_DIR / out, cfg, limit=int(limit) or None)
        job_status("mat")
        if (OUTPUT_DIR / out).exists():
            st.download_button("Ergebnis herunterladen", (OUTPUT_DIR / out).read_bytes(), file_name=out)

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

    st.markdown("**Mappings Hochschule neu bauen** (LLM, fortsetzbar)")
    from sem.bereiche.hochschule import build as hs_build

    c1, c2 = st.columns(2)
    if c1.button("OpenAlex-Themen feinmappen"):
        start_job("map-openalex", hs_build.build_openalex, cfg)
    if c2.button("Grundlagenthemen (LLM + GND)"):
        start_job("map-grundlagen", hs_build.build_grundlagen, cfg)
    job_status("map-openalex")
    job_status("map-grundlagen")

# --- Methodik -------------------------------------------------------------------------------
with tab_meth:
    st.markdown((ROOT / "METHODIK.md").read_text(encoding="utf-8"))
