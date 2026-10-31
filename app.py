"""Einfache Oberfläche: streamlit run app.py (Menüsprache Deutsch oder Englisch, Umschalter in der Seitenleiste)"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from sem import schema as S
from sem import seeds as seedlib
from sem import prozess, vorschau
from sem.bereiche import BEREICHE
from sem.bereiche.base import STANDARD_KONFIDENZ, STATUS_WERTE, filtergruppe
from sem.i18n import UI_SPRACHEN, t
from sem.llm import DEFAULT_MODEL, DEFAULT_WORKERS, LLMConfig, api_key_fuer
from sem.mapping.embeddings import DEFAULT_EMBEDDING_MODEL
from sem.paths import (MAPPING_DIR, OUTPUT_DIR, PRAEFIX, PROMPT_DIR, ROOT, VOCAB_DIR, ausgabe_pfad,
                       ausgabedateien)
from sem.prompts import PROMPT_FILES, placeholders, prompt_path

st.set_page_config(page_title="Synthetic educational materials", layout="wide")
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Menüsprache --------------------------------------------------------------------------------
_UI_STANDARD = os.environ.get("SEM_UI_SPRACHE", "de")
UI = st.sidebar.radio("Sprache / Language", list(UI_SPRACHEN), horizontal=True, format_func=UI_SPRACHEN.get,
                      index=list(UI_SPRACHEN).index(_UI_STANDARD) if _UI_STANDARD in UI_SPRACHEN else 0,
                      key="ui-sprache")


def T(text: str, **werte) -> str:
    """Text in der Menüsprache; Platzhalter {name} werden danach gefüllt."""
    out = t(text, UI)
    return out.format(**werte) if werte else out


def uk(name: str) -> str:
    """Schlüssel für Auswahlfelder mit übersetzten Optionen: Streamlit zeichnet die Beschriftung sonst nach dem
    Sprachwechsel nicht neu. Die bisherige Auswahl wird beim Wechsel übernommen."""
    return f"{name}@{UI}"


_ui_alt = st.session_state.get("_ui-alt")
if _ui_alt and _ui_alt != UI:
    for _k in [k for k in st.session_state if isinstance(k, str) and k.endswith(f"@{_ui_alt}")]:
        st.session_state[_k[:-len(_ui_alt)] + UI] = st.session_state.pop(_k)
st.session_state["_ui-alt"] = UI


@st.cache_resource
def bereich(key):
    return BEREICHE[key]()


def start_job(name: str, argv: list[str], cfg: LLMConfig):
    """Lauf als eigenen Prozess starten (python -m sem …): Die Oberfläche bleibt flüssig, und der Lauf geht weiter,
    auch wenn die Seite geschlossen oder die App neu gestartet wird."""
    args, env = prozess.llm_umgebung(cfg)
    try:
        prozess.starten(name, argv + args, env)
    except RuntimeError as e:
        st.error(str(e))


@st.fragment(run_every=1)
def job_status(name: str):
    s = prozess.stand(name)
    if not s:
        return
    with st.container(border=True):
        if s.get("total"):
            st.progress(min(1.0, (s["done"] + s["failed"]) / s["total"]), text=s["zeile"])
        elif s.get("zeile") and not s.get("fehler"):
            st.info(s["zeile"])
        elif s["laeuft"]:
            st.info(T("startet … (liest Seed-Datei und fertige Materialien, bei großen Läufen einige Sekunden)"))
        if s.get("fehler"):
            st.error(s["fehler"])
        c1, c2 = st.columns([1, 4])
        if s["laeuft"]:
            if s["angehalten"]:
                st.caption(T("Wird angehalten: Es startet keine neue Anfrage mehr, laufende werden noch beendet. "
                             "Halb fertige Materialien werden beim Fortsetzen neu erzeugt."))
            elif c1.button(T("Anhalten"), key=f"stop-{name}"):
                prozess.anhalten(name)
                st.rerun(scope="fragment")
            c2.caption(T("Läuft als eigener Prozess weiter, auch wenn diese Seite geschlossen wird. Stand: {zeit}",
                         zeit=s.get("aktualisiert", "–")))
        else:
            if c1.button(T("Ausblenden"), key=f"weg-{name}"):
                prozess.vergessen(name)
                st.rerun()
            if s.get("ende") is not None or s.get("fehler"):
                c2.caption(T("Beendet. Ausgabe des Prozesses: {datei}", datei=f"output/.prozesse/{name}.log"))


# --- Seitenleiste: LLM ---------------------------------------------------------------------
with st.sidebar:
    st.header(T("LLM-Anbindung"))
    st.caption(T("OpenAI-kompatibel. Leere Felder: Werte aus OPENAI_BASE_URL, OPENAI_API_KEY, LLM_MODEL."))
    env = LLMConfig.from_env()
    base_url = st.text_input("Base-URL", value=env.base_url or "", placeholder="https://api.openai.com/v1")
    model = st.text_input(T("Modell"), value=env.model or DEFAULT_MODEL)
    api_key = st.text_input("API-Key", type="password", placeholder=T("aus OPENAI_API_KEY") if env.api_key else "")
    if env.api_key and not api_key and (base_url or None) != env.base_url:
        st.warning(T("Andere Base-URL als in OPENAI_BASE_URL: Der Key aus OPENAI_API_KEY wird dafür nicht verwendet. "
                     "Bitte einen Key eintragen."))
    st.header(T("Geschwindigkeit"))
    workers = st.number_input(T("Worker (parallel)"), 1, 1000, env.workers or DEFAULT_WORKERS,
                              help=T("Materialien, die gleichzeitig erzeugt werden. Mehr Worker = schneller, "
                                     "solange das Rate-Limit des Anbieters reicht."))
    efforts = ["minimal", "low", "medium", "high", "Modellstandard"]
    effort = st.radio(T("Reasoning-Aufwand"), efforts, horizontal=True,
                      index=efforts.index(env.reasoning_effort or "Modellstandard"),
                      format_func=lambda e: T(e) if e == "Modellstandard" else e,
                      help=T("Nur für Reasoning-Modelle. low ist Standard: gemessen ein Drittel schneller als "
                             "der Modellstandard bei gleichen Prüfwerten. „Modellstandard“ sendet den Parameter "
                             "nicht (für Modelle, die ihn nicht kennen)."))
    flex = st.checkbox(T("Flex-Verarbeitung (halber Preis)"), value=False,
                       help=T("Nur bei OpenAI: halber Preis, dafür langsamere Antworten und bei Engpässen Ablehnungen, "
                              "die automatisch wiederholt werden. Gilt für Materialien und Mappings; bei Batch nicht "
                              "nötig, der ist schon zum halben Preis."))
    retries = st.number_input(T("Versuche pro Aufgabe"), 1, 20, 6)
    outage = st.number_input(T("Wartezeit bei API-Ausfall (Minuten)"), 1, 240, 10,
                             help=T("Danach pausiert der Lauf und lässt sich später fortsetzen."))
    st.header("Embeddings")
    emb_model = st.text_input(T("Embedding-Modell (lokal, CPU)"), value=DEFAULT_EMBEDDING_MODEL,
                              help=T("z. B. intfloat/multilingual-e5-small oder "
                                     "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
    cfg = LLMConfig(base_url=base_url or None, api_key=api_key_fuer(base_url, api_key, env), model=model,
                    workers=int(workers), max_retries=int(retries), outage_minutes=float(outage),
                    reasoning_effort=None if effort == "Modellstandard" else effort,
                    service_tier="flex" if flex else None)

# --- Helfer für Dateien in output/ ------------------------------------------------------------
@st.cache_data(max_entries=64)
def _zeilen(path: str, mtime: float) -> int:
    # binär zählen: eine 600-MB-Ausgabe als Text Zeile für Zeile zu prüfen dauert Sekunden, bei jedem Klick
    with open(path, "rb") as f:
        return sum(1 for l in f if l.strip())


def anzahl(n: int, eins: str, viele: str) -> str:
    return f"{n} {T(eins) if n == 1 else T(viele)}"


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
                  help=T("Feste Namensanfänge: seeds_ für Seed-Dateien, materialien_ für erzeugte Materialien. "
                         "Daran erkennt die App, welche Dateien in welche Auswahl gehören."))
    # Vorschlag im Schlüssel: ändern sich die Einstellungen, folgt der Name (sonst bliebe der erste Vorschlag stehen)
    frei = c2.text_input("Name", vorschlag, key=f"{key}:{vorschlag}", help=hilfe)
    c3.text_input(T("Endung"), ".jsonl", disabled=True, key=f"{key}-endung")
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
            if chk["uebereinstimmung"]:
                st.success(T("Inhaltstyp-Prüfung: erkannt „{typ}“ – passt zum Seed", typ=chk["erkannt_label"]))
            else:
                st.warning(T("Inhaltstyp-Prüfung: erkannt „{typ}“ – weicht vom Seed ab", typ=chk["erkannt_label"]))
    else:
        st.info(T("Das ist ein Seed: die Vorgaben für ein Material. Erzeugte Materialien zeigt der Umschalter "
                  "„Erzeugte Materialien“."))
    c1, c2 = st.columns([3, 2]) if vorschau.ist_material(rec) else (None, st.container())
    if c1 is not None:
        with c1:
            st.markdown(f"**{T('Volltext')}**")
            with st.container(border=True, height=520):
                st.markdown(rec[S.F_FULLTEXT])
    with c2:
        st.markdown(f"**{T('Metadaten aus dem Seed')}**")
        zeilen_meta = [(T(k), v) for k, v in vorschau.metadaten(rec).items()]
        st.table(pd.DataFrame(zeilen_meta, columns=[T("Feld"), T("Wert")]).set_index(T("Feld")))
        th = rec.get(S.F_THEMA, {})
        if th.get("beschreibung_de") or th.get("keywords_de"):
            with st.expander(T("Thema aus dem Seed")):
                st.write(th.get("beschreibung_de", ""))
                st.caption(", ".join(th.get("keywords_de", [])))
    if rec.get(S.F_QA):
        st.markdown(f"**{T('Frage-Antwort-Paare')}**")
        st.dataframe(pd.DataFrame(rec[S.F_QA]), hide_index=True, width="stretch")
    with st.expander(T("Herkunft je Feld")):
        st.json(rec.get(S.F_PROVENANCE, {}), expanded=False)
    with st.expander(T("Rohdaten (JSON)")):
        st.json(rec, expanded=False)


tab_seeds, tab_mat, tab_vorschau, tab_prompts, tab_map, tab_meth = st.tabs(
    ["Seeds", T("Materialien"), T("Vorschau"), "Prompts", "Mappings", T("Methodik")])

# --- Seeds ----------------------------------------------------------------------------------
with tab_seeds:
    st.subheader(T("Seeds erzeugen"))
    st.caption(T("Ohne LLM. Gleicher Startwert für den Zufall und gleiche Einstellungen ergeben dieselben Seeds."))
    STATUS_TEXT = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                   "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                   "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
    g1, g2 = st.columns(2)
    gewaehlt = g1.multiselect(T("Bildungsbereiche"), list(BEREICHE), default=[next(iter(BEREICHE))],
                              format_func=lambda k: T(bereich(k).label), key=uk("s-bereiche"),
                              help=T("Für jeden gewählten Bereich lassen sich Anzahl, Gleichverteilung, Quellen und "
                                     "Konfidenz einzeln einstellen."))
    konfidenz = g2.multiselect(T("Konfidenz der Zuordnungen (alle Bereiche)"), STATUS_WERTE, default=STANDARD_KONFIDENZ,
                               key=uk("s-konfidenz"),
                               help=T("Setzt alle Status-Filter der gewählten Bereiche auf diese Werte; je Bereich "
                                      "weiter einzeln änderbar. Standard: gesichert, wahrscheinlich und redaktionell; "
                                      "unsicher erst nach Prüfung.") + "\n\n" +
                               "\n".join(f"- **{w}**: {T(STATUS_TEXT[w])}" for w in STATUS_WERTE))
    konf_key = "-".join(konfidenz) or "keine"
    counts, balance, auswahl, leer = {}, {}, {}, []
    GRUPPEN = [("quellen", "Quellen und Belege"), ("konfidenz", "Konfidenz (Status der Zuordnungen)"),
               ("inhalt", "Weitere Merkmale")]
    for key in gewaehlt:
        b = bereich(key)
        st.markdown(f"#### {T(b.label)}")
        c1, c2 = st.columns([1, 2])
        counts[key] = c1.number_input(T("{bereich}: Anzahl", bereich=T(b.label)), 0, 1_000_000, 1000, step=500,
                                      key=f"n-{key}")
        opts = list(b.balance_options)
        balance[key] = c2.selectbox(T("{bereich}: gleich verteilen nach", bereich=T(b.label)), opts,
                                    index=opts.index(b.default_balance),
                                    format_func=lambda o, b=b: f"{o} – {T(b.balance_options[o])}", key=uk(f"bal-{key}"))
        filt = b.auswahl_optionen()
        if filt:
            with st.expander(T("{bereich}: welche Themen einbezogen werden (Quellen, Konfidenz, weitere Merkmale)",
                               bereich=T(b.label))):
                wahl = {}
                for gruppe, titel in GRUPPEN:
                    namen = [n for n, o in filt.items() if filtergruppe(n, o) == gruppe]
                    if not namen:
                        continue
                    st.markdown(f"**{T(titel)}**")
                    for name in namen:
                        o = filt[name]
                        std = konfidenz if gruppe == "konfidenz" else o["standard"]
                        # Schlüssel mit der globalen Konfidenz: ändert man sie, übernehmen alle Status-Filter den Wert
                        k = uk(f"f-{key}-{name}" + (f"-{konf_key}" if gruppe == "konfidenz" else ""))
                        wahl[name] = st.multiselect(T(o["label"]), list(o["werte"]), default=std, key=k,
                                                    help=T(o["beschreibung"]),
                                                    format_func=lambda w, o=o: f"{w} – {T(o['werte'][w])}")
                auswahl[key] = wahl
        n_themen = len(b.themen_auswahl(auswahl.get(key)))
        st.caption(T("{n} von {gesamt} Themen ausgewählt.", n=n_themen, gesamt=len(b.topics())))
        if not n_themen:
            leer.append(T(b.label))
    if leer:
        st.warning(T("Keine Themen ausgewählt für: {bereiche}. Filter erweitern.", bereiche=", ".join(leer)))
    rnd = st.number_input(T("Startwert für den Zufall"), 0, 2**31 - 1, 42,
                          help=T("Gleicher Startwert und gleiche Einstellungen ergeben dieselben Seeds."))
    ziel = dateiname_feld(T("Dateiname"), "seeds", "_".join(f"{k}{v}" for k, v in counts.items() if v) + f"_s{rnd}",
                          # Schlüssel mit Vorschlag: der Name folgt geänderten Anzahlen und Startwert
                          f"s-name-{'_'.join(f'{k}{v}' for k, v in counts.items() if v)}-{rnd}",
                          T("Frei wählbar, z. B. hochschule30000_s42"))
    vorhanden = ziel.exists()
    if vorhanden:
        st.warning(T("{datei} gibt es schon ({anzahl}) und wird beim Erzeugen überschrieben.",
                     datei=ziel.name, anzahl=anzahl(zeilen(ziel), "Seed", "Seeds")))
    if st.button(T("Seeds erzeugen"), type="primary", disabled=sum(counts.values()) == 0 or bool(leer)):
        try:
            with st.spinner(T("erzeuge …")):
                data = seedlib.build(counts, balance, int(rnd), auswahl)
                stats = seedlib.write(data, ziel, {"anzahl": counts, "gleichverteilung": balance,
                                                   "zufalls_seed": int(rnd),
                                                   "auswahl": {k: bereich(k).auswahl_pruefen(v)
                                                               for k, v in auswahl.items()}})
        except ValueError as e:  # z. B. Filter ohne verbleibende Themen
            st.error(str(e))
            st.stop()
        st.success(T("{n} Seeds in output/{datei}. Weiter im Reiter „Materialien“.", n=stats["anzahl"], datei=ziel.name))
        fehlt = [k for k, v in stats.get("lokale_daten", {}).items() if v is None]
        if fehlt:
            st.warning(T("Lokale Dateien fehlen ({dateien}): Die Seeds weichen von einem Lauf mit diesen Dateien ab "
                         "(siehe data/README.md, data/lokal/).", dateien=", ".join(fehlt)))
        with st.expander(T("Statistik")):
            st.json(stats, expanded=False)
        download(ziel, T("Seeds herunterladen"), "dl-seeds")

# --- Materialien ----------------------------------------------------------------------------
with tab_mat:
    st.subheader(T("Materialien erzeugen"))
    st.caption(T("Pro Seed: Volltext, dann je ein Aufruf für Titel, Beschreibung, Keywords, Inhaltstyp-Prüfung und "
                 "Frage-Antwort-Paare. Ein erneuter Start setzt fort und überspringt Fertiges."))
    from sem.laeufe import freier_name, sperre_aufheben, unfertige_laeufe

    laeuft_mat = prozess.laeuft("mat")
    job_status("mat")
    unfertig = {L["name"]: L for L in unfertige_laeufe()}
    aufgabe = "neu"
    if unfertig:
        aufgabe = st.radio(T("Aufgabe"), ["neu", "fortsetzen"], horizontal=True, key=uk("m-aufgabe"),
                           label_visibility="collapsed",
                           format_func=lambda a: T("Neuen Lauf starten") if a == "neu"
                           else T("Unfertigen Lauf fortsetzen ({n})", n=len(unfertig)))

    if aufgabe == "fortsetzen":
        # Läufe, die ihr Ziel nicht erreicht haben (z. B. nach Internet-Unterbrechung): Einstellungen zeigen, dann starten
        st.caption(T("Läufe, die ihr Ziel nicht erreicht haben, z. B. nach einer Internet-Unterbrechung oder mit "
                     "fehlgeschlagenen Materialien. Fortsetzen nutzt die Einstellungen des Laufs und erzeugt nur, was "
                     "fehlt; „Neu starten“ beginnt mit denselben Einstellungen unter neuem Namen, die bisherige Datei "
                     "bleibt. Worker und Standard oder Flex wählst du hier."))
        L = unfertig[st.selectbox(T("Lauf (zuletzt bearbeitet zuerst)"), list(unfertig), key="lauf-wahl",
                                  format_func=lambda n: f"{n} – {unfertig[n]['fertig']} / {unfertig[n]['ziel']}")]
        with st.container(border=True):
            m1, m2, m3 = st.columns(3)
            m1.metric(T("Fertig"), f"{L['fertig']} / {L['ziel']}")
            m2.metric(T("Offen"), max(0, L["ziel"] - L["fertig"]))
            m3.metric(T("davon zuletzt fehlgeschlagen"), L["fehlgeschlagen"])
            st.caption(" · ".join([
                T("Seed-Datei: {datei}", datei=Path(L["seeds"]).name),
                T("Modell: {modell}", modell=L["modell"]),
                T("Reasoning-Aufwand: {aufwand}", aufwand=L["reasoning_effort"] or T("Modellstandard")),
                T("Ausgabesprache: {sprache}", sprache=L["ausgabesprache"]),
                T("Inhaltstyp-Prüfung: {wert}", wert=T("ja") if L["inhaltstyp_pruefen"] else T("nein")),
                T("zuletzt: {verarbeitung}, {zeit}", verarbeitung=L["verarbeitung"], zeit=L["zuletzt"])]))
            batch = L["verarbeitung"] == "batch"
            c1, c2 = st.columns(2)
            tier = "batch" if batch else c1.selectbox(
                T("Verarbeitung"), ["sofort", "flex"], index=int(L["verarbeitung"] == "flex"),
                key=f"lauf-tier-{L['name']}", format_func=lambda v: T("Standard") if v == "sofort" else "Flex",
                help=T("Flex: halber Preis, dafür langsamere Antworten."))
            w = cfg.workers if batch else c2.number_input(T("Worker (parallel)"), 1, 1000, cfg.workers,
                                                          key=f"lauf-worker-{L['name']}")
            lauf_cfg = dataclasses.replace(cfg, model=L["modell"], reasoning_effort=L["reasoning_effort"],
                                           workers=int(w), service_tier="flex" if tier == "flex" else None)
            gesperrt = L["gesperrt"] and not laeuft_mat
            if gesperrt:
                st.warning(T("Die Ausgabe ist gesperrt: Ein anderer Prozess arbeitet daran (z. B. auf der Kommandozeile), "
                             "oder ein abgebrochener Lauf hat die Sperre hinterlassen."))
            if not Path(L["seeds"]).exists():
                st.error(T("Seed-Datei fehlt"))
            else:
                b1, b2, b3 = st.columns(3)
                neu_ziel = freier_name(Path(L["ausgabe"]))
                for spalte, label, ziel, primaer in ((b1, T("Fortsetzen"), Path(L["ausgabe"]), True),
                                                     (b2, T("Neu starten"), neu_ziel, False)):
                    if not spalte.button(label, key=f"lauf-{label}", type="primary" if primaer else "secondary",
                                         disabled=laeuft_mat or L["gesperrt"],
                                         help=None if primaer else T("als {datei}", datei=neu_ziel.name)):
                        continue
                    if batch:
                        from sem.batch import BatchLauf

                        try:
                            st.session_state["batch-stand"] = BatchLauf(
                                Path(L["seeds"]), ziel, lauf_cfg, limit=L["limit"],
                                inhaltstyp_pruefen=L["inhaltstyp_pruefen"], ausgabesprache=L["ausgabesprache"]).weiter()
                        except (ValueError, RuntimeError) as e:
                            st.error(str(e))
                    elif primaer:
                        start_job("mat", ["materialien", "--fortsetzen", L["name"],
                                          *([] if tier == "flex" else ["--ohne-flex"])], lauf_cfg)
                    else:
                        start_job("mat", ["materialien", "--seeds", L["seeds"], "--out", str(ziel),
                                          "--sprache", L["ausgabesprache"]]
                                  + (["--limit", str(L["limit"])] if L["limit"] else [])
                                  + ([] if L["inhaltstyp_pruefen"] else ["--ohne-inhaltstyp-pruefung"]), lauf_cfg)
                    st.rerun()
                if gesperrt and b3.button(
                        T("Sperre aufheben"), key="lauf-sperre",
                        help=T("Die Sperrdatei bleibt nach einem Absturz liegen. Nur aufheben, wenn sicher kein "
                               "anderer Lauf (z. B. auf der Kommandozeile) auf dieser Datei arbeitet.")):
                    sperre_aufheben(Path(L["ausgabe"]))
                    st.rerun()

    else:
        seed_dateien = ausgabedateien("seeds")
        if not seed_dateien:
            st.info(T("Noch keine Seed-Dateien (seeds_…) in output/. Erst im Reiter „Seeds“ erzeugen."))
        else:
            src = st.selectbox(T("Seed-Datei (neueste zuerst)"), seed_dateien, key="m-src",
                               format_func=lambda p: f"{p.name} – {anzahl(zeilen(p), 'Seed', 'Seeds')}",
                               help=T("Hier stehen nur Dateien, die mit seeds_ beginnen."))
            n_seeds = zeilen(src)
            limit = st.number_input(T("Wie viele Seeds verarbeiten? (0 = alle {n})", n=n_seeds), 0, n_seeds, 0,
                                    help=T("Es werden die ersten N Seeds der Datei genommen."))
            out = dateiname_feld(T("Ausgabedatei"), "materialien", src.stem.removeprefix(PRAEFIX["seeds"]),
                                 f"m-out-{src.name}", T("Standard: derselbe Name wie die Seed-Datei"))
            fertig_n = zeilen(out)
            if fertig_n:
                st.info(T("{datei} enthält schon {anzahl}. Start setzt fort und überspringt Fertiges. Für einen neuen Lauf "
                          "einen anderen Namen wählen.", datei=out.name, anzahl=anzahl(fertig_n, "Material", "Materialien")))
            lrt_check = st.checkbox(T("Inhaltstyp am fertigen Material prüfen"), value=True,
                                    help=T("Bestimmt den Inhaltstyp ohne Kenntnis des Seeds und protokolliert, ob er "
                                           "passt. Ein Aufruf mehr je Material."))
            from sem.generate import SPRACHEN
            sprache = st.selectbox(T("Ausgabesprache"), list(SPRACHEN), format_func=lambda c: T(SPRACHEN[c]),
                                   key=uk("m-sprache"),
                                   help=T("Sprache von Volltext, Titel, Beschreibung, Keywords und Fragen. Die Vorgaben "
                                          "bleiben deutsch; bei einer anderen Sprache wird der Hinweis aus der Vorlage "
                                          "„ausgabesprache“ (Reiter „Prompts“) angehängt. Steht in cclom:general_language."))
            laeuft = laeuft_mat
            modus = st.radio(T("Verarbeitung"), ["sofort", "batch"], horizontal=True, key=uk("m-modus"),
                             format_func=lambda m: T("Sofort") if m == "sofort" else T("Batch (halber Preis, Runden bis 24 h)"),
                             help=T("Sofort: Material für Material mit den Workern aus der Seitenleiste (mit Flex zum "
                                    "halben Preis). Batch: alle Anfragen gesammelt an die Batch-Schnittstelle von OpenAI, "
                                    "eine Runde je Stufe; gleiche Prompts, Prüfungen und Ergebnisse."))
            if modus == "sofort":
                st.caption(T("Einstellungen aus der Seitenleiste: {modell}, {worker} Worker, Reasoning-Aufwand {aufwand}.",
                             modell=cfg.model, worker=cfg.workers, aufwand=cfg.reasoning_effort or T("Modellstandard"))
                           + (" Flex." if cfg.service_tier == "flex" else ""))
                if st.button(T("Start / Fortsetzen"), type="primary", disabled=laeuft):
                    start_job("mat", ["materialien", "--seeds", str(src), "--out", str(out), "--sprache", sprache]
                              + (["--limit", str(int(limit))] if limit else [])
                              + ([] if lrt_check else ["--ohne-inhaltstyp-pruefung"]), cfg)
                    st.rerun()
            else:
                from sem.batch import BatchLauf, status_zeile

                st.caption(T("Je Stufe eine Runde (Volltext, Titel, Beschreibung, Keywords, Abschluss), jede bis zu 24 "
                             "Stunden, oft schneller. „Batch: abrufen und weiter“ holt Fertiges ab und schickt die nächste Runde; "
                             "dazwischen kann die Seite geschlossen werden. Automatisch bis zum Ende: python -m sem "
                             "materialien --batch --warten"))
                if st.button(T("Batch: abrufen und weiter"), type="primary", disabled=laeuft):
                    try:
                        with st.spinner(T("frage Batches ab …")):
                            st.session_state["batch-stand"] = BatchLauf(
                                src, out, cfg, limit=int(limit) or None, inhaltstyp_pruefen=lrt_check,
                                ausgabesprache=sprache).weiter()
                    except (ValueError, RuntimeError) as e:
                        st.error(str(e))
                stand = st.session_state.get("batch-stand")
                if stand:
                    (st.success if stand["abgeschlossen"] else st.info)(status_zeile(stand))
            if out.exists() and not laeuft:
                letzte = letzter_datensatz(out)
                if letzte and st.toggle(T("Zuletzt erzeugtes Material zeigen (alle im Reiter „Vorschau“)"), key="mat-letzte"):
                    with st.container(border=True):
                        zeige(letzte)
                download(out, T("Materialien herunterladen"), "dl-mat")

# --- Vorschau ---------------------------------------------------------------------------------
with tab_vorschau:
    st.subheader(T("Vorschau"))
    art = st.radio(T("Anzeigen"), ["materialien", "seeds"], horizontal=True, key=uk("v-art"),
                   format_func=lambda a: {"materialien": T("Erzeugte Materialien (materialien_…)"),
                                          "seeds": "Seeds (seeds_…)"}[a])
    dateien = ausgabedateien(art)
    if not dateien:
        st.info(T("Noch keine erzeugten Materialien in output/ – erst im Reiter „Materialien“ starten.")
                if art == "materialien" else T("Noch keine Seeds in output/."))
    else:
        einheit = ("Material", "Materialien") if art == "materialien" else ("Seed", "Seeds")
        datei = st.selectbox(T("Datei (neueste zuerst)"), dateien, key=f"v-datei-{art}",
                             format_func=lambda p: f"{p.name} – {anzahl(zeilen(p), *einheit)}")
        gesamt = zeilen(datei)
        limit = None
        if gesamt > 5000:
            limit = st.number_input(T("Datensätze laden (von {n}; große Dateien brauchen Zeit)", n=gesamt), 100,
                                    gesamt, 5000, step=1000, key=f"v-limit-{art}",
                                    help=T("Suche und Blättern beziehen sich auf die geladenen Datensätze."))
        # Sind schon mehr Zeilen da als geladen werden, ändert ein laufender Lauf (hängt hinten an) nichts an den
        # geladenen: nicht bei jedem Klick neu laden
        rows = _lade(str(datei), 0 if limit and gesamt > limit else datei.stat().st_mtime, limit)
        c1, c2 = st.columns([2, 1])
        such = c1.text_input(T("Suchen (Thema, Fach, Inhaltstyp, Titel, Keyword, id)"), key="v-such").casefold()
        treffer = [r for r in rows if such in vorschau.suchtext(r)] if such else rows
        c2.metric(T("Datensätze"), f"{len(treffer)} / {len(rows)}")
        if treffer:
            if st.session_state.get("v-nr", 1) > len(treffer):
                st.session_state["v-nr"] = 1
            b1, b2 = st.columns([1, 4])
            if b1.button(T("Zufällig"), key="v-zufall"):
                import random

                st.session_state["v-nr"] = random.randint(1, len(treffer))
            nr = b2.number_input(T("Nr."), 1, len(treffer), key="v-nr")
            zeige(treffer[int(nr) - 1])

# --- Prompts --------------------------------------------------------------------------------
with tab_prompts:
    st.subheader(T("Prompts bearbeiten"))
    st.caption(T("Die Prompts selbst sind deutsch; die Sprache der erzeugten Inhalte wählt man im Reiter "
                 "„Materialien“ (Ausgabesprache)."))
    scope = st.selectbox(T("Gültig für"), ["alle Bereiche"] + list(BEREICHE),
                         format_func=lambda s: T("alle Bereiche") if s == "alle Bereiche" else T(bereich(s).label), key=uk("p-scope"))
    which = st.selectbox("Prompt", PROMPT_FILES)
    bkey = None if scope == "alle Bereiche" else scope
    path = prompt_path(which, bkey)
    target = PROMPT_DIR / f"{which}.md" if bkey is None else PROMPT_DIR / bkey / f"{which}.md"
    st.caption(T("Quelle: {quelle} · speichert nach: {ziel}", quelle=path.relative_to(ROOT),
                 ziel=target.relative_to(ROOT)))
    text = st.text_area(T("Text"), path.read_text(encoding="utf-8"), height=320, key=f"p-{scope}-{which}")
    st.caption(T("Platzhalter: {liste} · Jedes Material speichert den Hash der verwendeten Prompts, Änderungen "
                 "bleiben so nachvollziehbar.", liste=", ".join(placeholders(text)) or T("keine")))
    if st.button(T("Speichern")):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.strip() + "\n", encoding="utf-8")
        st.success(T("gespeichert – gilt für den nächsten Start"))

# --- Mappings -------------------------------------------------------------------------------
with tab_map:
    st.subheader(T("Vokabulare und Mappings"))
    st.caption(T("Alle Dateien sind auch einzeln nutzbar. Beschreibung der Felder: data/README.md"))
    groups = {T("Vokabulare"): sorted(VOCAB_DIR.glob("*.json"))}
    for d in sorted(p for p in MAPPING_DIR.iterdir() if p.is_dir()):
        groups[f"Mappings {d.name}"] = sorted(p for p in d.iterdir() if p.suffix in (".json", ".jsonl", ".csv"))
    for title, paths in groups.items():
        st.markdown(f"**{title}**")
        for p in paths:
            with st.expander(f"{p.name} ({p.stat().st_size // 1024} KB)"):
                download(p, T("Herunterladen"), f"dl-{p}")
                if not st.toggle(T("Inhalt anzeigen"), key=f"show-{p}"):
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

    st.markdown(T("**Mappings neu bauen** (fortsetzbar; bereits Erledigtes wird übersprungen)"))
    for key in BEREICHE:
        b = bereich(key)
        schritte = {x.key: x for x in b.mapping_schritte()}
        if not schritte:
            continue
        with st.container(border=True):
            st.markdown(f"**{T(b.label)}**")
            if st.toggle(T("Aktuellen Stand des Mappings anzeigen"), key=f"stat-{key}"):
                st.json(b.mapping_statistik())
            wahl = st.multiselect(T("Schritte"), list(schritte), default=[], key=uk(f"ms-{key}"),
                                  placeholder=T("Schritte wählen (die Daten im Repo sind fertig gebaut)"),
                                  format_func=lambda k, s=schritte: f"{T(s[k].label)}{' (LLM)' if s[k].llm else ''}")
            if any(schritte[k].llm for k in wahl):
                st.warning(T("Ausgewählte Schritte rufen das LLM auf und verursachen Kosten. Bereits Erledigtes "
                             "wird übersprungen."))
            c1, c2 = st.columns(2)
            lim = c1.number_input(T("Nur die ersten N Themen (0 = alle)"), 0, 100_000, 0, key=f"ml-{key}")
            stimmen = c2.number_input(T("Stimmen je Zuordnung"), 1, 7, 3, key=f"mv-{key}")
            wlo_txt = st.text_input(T("WLO-Exporte (CSV-Pfade, durch Komma getrennt; bleiben lokal)"), key=f"mw-{key}")
            job = f"map-{key}"
            wlo = [x.strip() for x in wlo_txt.split(",") if x.strip()]
            if st.button(T("Start / Fortsetzen"), key=f"mb-{key}", disabled=prozess.laeuft(job) or not wahl):
                start_job(job, ["mappings", key, *[k for k in schritte if k in wahl], "--stimmen", str(int(stimmen)),
                                "--embedding-modell", emb_model]
                          + (["--limit", str(int(lim))] if lim else []) + (["--wlo", *wlo] if wlo else []), cfg)
                st.rerun()
            job_status(job)

# --- Methodik -------------------------------------------------------------------------------
with tab_meth:
    methodik = ROOT / ("METHODIK.md" if UI == "de" else "METHODOLOGY.md")
    text = (methodik if methodik.exists() else ROOT / "METHODIK.md").read_text(encoding="utf-8")
    st.markdown("\n".join(z for z in text.splitlines() if not z.startswith(("*English ·", "*[English]"))))
