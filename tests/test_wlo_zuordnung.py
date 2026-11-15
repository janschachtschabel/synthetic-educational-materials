"""WLO-Abstraktion für Erwachsenen- und Elementarbildung: Zuordnung ohne LLM (sem/wlo_zuordnung.py), Themen-Dateien
und Seed-Filter, mit einem Wort-Embedder statt des Modells."""

import json

import numpy as np
import pytest

from sem import wlo_seeds as WS
from sem import wlo_themen as W
from sem import wlo_zuordnung as Z
from sem.bereiche import get
from sem.bereiche.base import WLO_THEMA_PREFIX, build_seeds, wlo_passt

CTX = W.CTX
DISC = Z.DISC
WOERTER = ["englisch", "sprachkurs", "schweiß", "tier", "unterricht", "kita", "kindergarten", "grundschule",
           "malerei", "religion"]


class WortEmbedder:
    """Ein Merkmal je Wort aus WOERTER (plus Grundrauschen), normiert."""
    model = "wort"

    def encode(self, texts, art="ziel", on_progress=None):
        def v(t):
            t = t.casefold()
            x = np.array([float(w in t) for w in WOERTER] + [0.05])
            return x / np.linalg.norm(x)
        return np.stack([v(t) for t in texts]) if texts else np.zeros((0, len(WOERTER) + 1))


def _abstraktion(gid, thema, fach, stufen, nodes, allgemeiner=None, vorschlag=False):
    return {"id": gid, "node_ids": nodes, "thema": thema, "thema_allgemeiner": allgemeiner, "fachbegriffe": ["Begriff"],
            "entfernt": [], "fach": [W.ref(u) for u in fach], "stufe": [W.ref(u) for u in stufen],
            "vorschlag": vorschlag, "seed_tauglich": True, "pruefung": {"kopie": False}}


EB, EL = f"{CTX}erwachsenenbildung", f"{CTX}elementarbereich"
ROWS = [
    _abstraktion("e1", "Englische Konnektoren", [], [EB], ["n1"]),
    _abstraktion("e2", "englische Konnektoren", [f"{DISC}720"], [EB], ["n2"]),  # gleiches Thema, anderes Fach
    _abstraktion("e3", "WIG-Schweißen von Rohren", [f"{DISC}040"], [f"{CTX}berufliche_bildung", EB], ["n3"]),
    _abstraktion("e4", "Holmium als chemisches Element", [f"{DISC}100"], [f"{CTX}sekundarstufe_1", EB], ["n4"]),
    _abstraktion("k1", "Tiere im Wald", [f"{DISC}28010"], [EL, f"{CTX}grundschule"], ["n5"], vorschlag=True),
    _abstraktion("k2", "Unterrichtsplanung in der Grundschule", [f"{DISC}720"], [EL], ["n6"]),
]
VORHANDEN = {
    "erwachsenenbildung": [{"id": "gnd:1", "label_de": "Aquarellmalerei", "herkunft": "gnd",
                            "fachgebiet": [{"notation": "2.07"}]}],
    "elementar": [{"id": "gnd:2", "label_de": "Tiere", "herkunft": ["llm"], "art": "kinderthema",
                   "altersgruppe": "ue3", "bildungsbereich": [{"id": "mint"}]},
                  {"id": "gnd:3", "label_de": "Tiere im Wald", "herkunft": ["wlo"], "art": "kinderthema",
                   "altersgruppe": "ue3", "bildungsbereich": [{"id": "umwelt_bne"}]}],
}


def _bauen(ohne_lokal):
    p = ohne_lokal / "wlo_abstraktion" / "abstraktion.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8")
    return WS.bauen([p], bereiche=("erwachsenenbildung", "elementar"), embedder=WortEmbedder(), gnd={},
                    vorhanden=VORHANDEN, berufe=["Schweißnähte prüfen"], on_progress=lambda *_: None)


def test_eb_zuordnung_und_eignung(ohne_lokal):
    st = _bauen(ohne_lokal)
    eb = {t["label_de"].lower(): t for t in WS.themen("erwachsenenbildung")}
    assert len(eb) == 3  # e1 und e2 zusammengefasst (über alle Fächer)
    k = eb["englische konnektoren"]
    assert k["id"].startswith(WLO_THEMA_PREFIX + "erwachsenenbildung:") and k["herkunft"] == "wlo_abstraktion"
    assert k["fachgebiet"][0]["notation"] == "4.06"  # Sprache aus dem Muster „englisch“
    assert k["programmbereich"][0]["notation"] == "4" and k["wlo"]["gewicht"] == 2
    assert k["wlo"]["eignung"] == {"status": "geeignet", "gruende": [], "beruf_abstand": k["wlo"]["eignung"]["beruf_abstand"]}
    s = eb["wig-schweißen von rohren"]
    assert s["wlo"]["eignung"]["status"] == "fraglich" and "eher_berufsbildung" in s["wlo"]["eignung"]["gruende"]
    h = eb["holmium als chemisches element"]
    assert "schulstoff" in h["wlo"]["eignung"]["gruende"] and h["fachgebiet"][0]["notation"] in ("1.10", "6.07")
    b = st["bereiche"]["erwachsenenbildung"]
    assert b["themen"] == 3 and b["eignung"] == {"geeignet": 1, "fraglich": 2}
    assert b["uebersicht"]["alle"]["themen"] == 3 and "einzigartig_ohne_vorhandene" in b["uebersicht"]
    text = (ohne_lokal / "wlo_abstraktion" / "themen_erwachsenenbildung.jsonl").read_text(encoding="utf-8")
    assert "titel" not in text and "beschreibung_de" not in text


def test_elementar_zuordnung_und_eignung(ohne_lokal):
    st = _bauen(ohne_lokal)
    el = {t["label_de"]: t for t in WS.themen("elementar")}
    t = el["Tiere im Wald"]
    assert t["herkunft"] == ["wlo_abstraktion"] and t["altersgruppe"] == "ue3" and t["art"] == "kinderthema"
    assert t["bildungsbereich"][0]["id"] in ("mint", "umwelt_bne") and t["bildungsbereich"][0]["uri"].endswith(
        "#" + t["bildungsbereich"][0]["id"])
    assert t["wlo"]["vorschlag"] and t["wlo"]["eignung"]["status"] == "geeignet"
    assert {x["id"] for x in t["verknuepft_mit"] if x["art"] == "vorhanden"} >= {"gnd:3"} or \
        t["bildungsbereich"][0]["id"] == "mint"
    u = el["Unterrichtsplanung in der Grundschule"]
    assert "schulbezug" in u["wlo"]["eignung"]["gruende"] and u["art"] == "fachthema"
    assert st["bereiche"]["elementar"]["eignung_gruende"]["schulbezug"] == 1


def test_verknuepfung_im_selben_fachgebiet():
    neu = [{"id": "w:1", "label_de": "Aquarellmalerei", "fachgebiet": [{"notation": "2.07"}]},
           {"id": "w:2", "label_de": "Aquarellmalerei", "fachgebiet": [{"notation": "1.01"}]}]
    z = WS.verknuepfen(neu, VORHANDEN["erwachsenenbildung"], WortEmbedder())
    assert [x["id"] for x in neu[0]["verknuepft_mit"]] == ["gnd:1"] and neu[1]["verknuepft_mit"] == []
    assert z["vorhanden_gnd"] == 1


def test_produktname():
    assert Z.produktname("Tabellenkalkulation mit Microsoft Excel") and Z.produktname("Interaktive Inhalte mit H5P")
    assert not Z.produktname("Tabellenkalkulation") and not Z.produktname("Zugänglichkeit von Bildungsmaterialien")


def test_waehlen_bonus_und_status():
    ziele = ["a", "b", "c"]
    zeile = np.array([0.80, 0.79, 0.70])
    assert Z.waehlen(zeile, ziele, {}) == ("a", "wahrscheinlich", pytest.approx(0.80))
    assert Z.waehlen(zeile, ziele, {"a": 0.015})[:2] == ("a", "gesichert")
    assert Z.waehlen(zeile, ziele, {"b": 0.015})[:2] == ("b", "wahrscheinlich")
    assert Z.waehlen(zeile, ziele, {"c": 0.015})[:2] == ("a", "unsicher")


def test_filter_wlo_eignung_und_zuordnung():
    t = {"id": WLO_THEMA_PREFIX + "elementar:x", "wlo": {"vorschlag": False, "eignung": {"status": "fraglich"}},
         "mapping": {"status": "unsicher"}}
    assert not wlo_passt(t, {"wlo_eignung": ["geeignet"]})
    assert wlo_passt(t, {"wlo_eignung": ["geeignet", "fraglich"], "wlo_zuordnung": ["unsicher"]})
    assert not wlo_passt(t, {"wlo_eignung": ["geeignet", "fraglich"], "wlo_zuordnung": ["gesichert"]})
    assert wlo_passt({"id": "gnd:1"}, {"wlo_eignung": []})
    for b in ("erwachsenenbildung", "elementar"):
        o = get(b).auswahl_optionen()
        assert o["wlo_eignung"]["standard"] == ["geeignet"]
        assert o["wlo_zuordnung"]["standard"] == ["gesichert", "wahrscheinlich", "unsicher"]


@pytest.mark.parametrize("bereich, filter_", [("erwachsenenbildung", "quellen"), ("elementar", "herkunft")])
def test_seeds_mit_wlo_themen(ohne_lokal, bereich, filter_):
    b = get(bereich)
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    _bauen(ohne_lokal)
    b = get(bereich)
    wlo = [t for t in b.topics() if t["id"].startswith(WLO_THEMA_PREFIX)]
    assert wlo
    assert not [s for s in build_seeds(b, 30, seed=1) if s["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)]
    std = b.standard_auswahl()
    an = {filter_: std[filter_] + ["wlo_abstraktion"], "wlo_anteil": ["50"], "wlo_eignung": ["geeignet", "fraglich"]}
    seeds = build_seeds(b, 20, seed=1, auswahl=an)
    ws = [s for s in seeds if s["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)]
    assert len(ws) == 10
    belege = ws[0]["synth:provenance"]["synth:thema"]["belege"]
    assert "wlo" in belege["erfuellt"] and belege["wlo"]["nodes"][0].startswith("https://")
    # Standard der Eignung: fragliche Themen fallen weg
    nur = build_seeds(b, 10, seed=1, auswahl={filter_: ["wlo_abstraktion"]})
    ids = {s["synth:thema"]["id"] for s in nur}
    fraglich = {t["id"] for t in wlo if t["wlo"]["eignung"]["status"] == "fraglich"}
    assert ids and not ids & fraglich
