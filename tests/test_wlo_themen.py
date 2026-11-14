"""WLO-Themen-Abstraktion (sem/wlo_themen.py) und Themen für die Seeds (sem/wlo_seeds.py), ohne API und ohne
Embedding-Modell."""

import json
from pathlib import Path

import numpy as np
import pytest

from sem import wlo_seeds as WS
from sem import wlo_themen as W
from sem.bereiche import get
from sem.bereiche.base import WLO_THEMA_PREFIX, _teile_wlo, build_seeds
from sem.llm import LLMConfig
from test_batch import FakeOpenAI

CTX = W.CTX
DISC = "http://w3id.org/openeduhub/vocabs/discipline/"
HFS = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"
SPALTEN = ["properties.sys:node-uuid", "properties.cclom:title", "properties.cclom:general_description",
           "properties.ccm:taxonid", "properties.ccm:oeh_taxonid_university", "properties.ccm:educationalcontext",
           "title"]


def _csv(tmp_path, zeilen) -> Path:
    p = tmp_path / "wlo.csv"
    p.write_text(";".join(SPALTEN) + "\n" + "".join(";".join(z) + "\n" for z in zeilen), encoding="utf-8")
    return p


# --- Lesen, null, Dubletten ----------------------------------------------------------------------------

def test_null_wird_bereinigt_auch_in_listen():
    assert W.text(" null ") == "" and W.text("Null") == "" and W.text("Nullstellen") == "Nullstellen"
    assert W.liste("null, a ,, NULL,b") == ["a", "b"]
    assert W.liste("") == [] and W.liste(None) == []


def test_lesen_ueberspringt_leere_und_filtert_vokabular(tmp_path):
    p = _csv(tmp_path, [
        ["n1", "Brüche addieren", "null", f"{DISC}380, null", "", f"{CTX}grundschule, legal"],
        ["n2", "null", "null", f"{DISC}380", "", f"{CTX}grundschule"],          # ohne Titel und Beschreibung
        ["n3", "", "Nur Beschreibung", f"{HFS}n37, unbekannt", "", "null"],
        ["n4", "", "", "", "", "", "Titel aus der Spalte title"],
    ])
    rows = list(W.lesen(p))
    assert [r["node"] for r in rows] == ["n1", "n3", "n4"]
    assert rows[0]["faecher"] == [f"{DISC}380"] and rows[0]["stufen"] == [f"{CTX}grundschule"]
    assert rows[0]["beschreibung"] == ""
    assert rows[1]["faecher"] == [f"{HFS}n37"] and rows[1]["stufen"] == []
    assert rows[2]["titel"] == "Titel aus der Spalte title"


def test_dublettenschluessel_normalisiert_und_behaelt_alle_nodes():
    a = W.schluessel("Brüche addieren!", "Wir addieren  Brüche.")
    assert a == W.schluessel("brüche addieren", "wir addieren brüche")
    assert a != W.schluessel("Brüche addieren", "Wir subtrahieren Brüche.")
    # gekürzt wie im Prompt: Unterschiede nach 800 Zeichen zählen nicht
    assert W.schluessel("T", "x" * 800 + "a") == W.schluessel("T", "x" * 800 + "b")
    g = W.gruppieren([
        {"node": "n1", "titel": "Brüche!", "beschreibung": "B", "faecher": [f"{DISC}380"], "stufen": []},
        {"node": "n2", "titel": "brüche", "beschreibung": "b", "faecher": [f"{DISC}100"], "stufen": [f"{CTX}schule"]},
        {"node": "n3", "titel": "Anderes", "beschreibung": "b", "faecher": [], "stufen": []},
    ])
    assert len(g) == 2
    eins = next(x for x in g.values() if len(x["node_ids"]) == 2)
    assert eins["node_ids"] == ["n1", "n2"] and eins["faecher"] == [f"{DISC}380", f"{DISC}100"]
    assert eins["stufen"] == [f"{CTX}schule"]


def _gruppe(gid, faecher, stufen, titel="T", beschr="B"):
    return {"id": gid, "node_ids": [gid + "-n"], "faecher": faecher, "stufen": stufen, "titel": titel,
            "beschreibung": beschr}


def test_stichprobe_geschichtet_und_ohne_stufe():
    gruppen = {}
    for i in range(30):
        gruppen[f"p{i}"] = _gruppe(f"p{i}", [f"{DISC}380"], [f"{CTX}grundschule"])
    for i in range(3):
        gruppen[f"h{i}"] = _gruppe(f"h{i}", [f"{HFS}n37"], [f"{CTX}hochschule"])
    for i in range(10):
        gruppen[f"o{i}"] = _gruppe(f"o{i}", [], [])
    assert len(W.auswaehlen(gruppen, "weglassen")) == 33  # ohne Stufe fällt weg
    assert len(W.auswaehlen(gruppen)) == 43 and len(W.auswaehlen(gruppen, "nur-thema")) == 43  # Standard vorschlag
    assert len(W.auswaehlen(gruppen, pool="ohne_stufe")) == 10
    assert len(W.auswaehlen(gruppen, pool="maskiert")) == 33
    s = W.auswaehlen(gruppen, "weglassen", limit=10)
    assert len(s) == 10 and sum(x.startswith("h") for x in s) == 3  # kleine Schicht ganz, Rest an die große
    assert s == W.auswaehlen(gruppen, "weglassen", limit=10)  # fest zum Startwert
    with pytest.raises(ValueError):
        W.auswaehlen(gruppen, "irgendwas")


# --- Prüfung -------------------------------------------------------------------------------------------

def test_kopierpruefung():
    titel = "Die Addition gleichnamiger Brüche im Alltag"
    k = W.kopierpruefung("Addition gleichnamiger Brüche im Alltag", [], titel, "")
    assert k["kopie_4w_titel"] and k["kopie"]
    k = W.kopierpruefung("Rechnen mit Brüchen", ["Bruch"], titel, "")
    assert not k["kopie"] and k["aehnlichkeit_titel"] < 0.8
    # Fachbegriffe zählen nicht als übernommene Formulierung
    fb = ["Satz des Pythagoras"]
    assert not W.vier_woerter("Satz des Pythagoras im Dreieck", "Der Satz des Pythagoras erklärt", fb)
    assert W.vier_woerter("Satz des Pythagoras im Dreieck", "Satz des Pythagoras im Dreieck", [])
    # Beschreibung
    k = W.kopierpruefung("Kreislauf des Wassers in der Natur", [], "Wasser", "Der Kreislauf des Wassers in der Natur")
    assert k["kopie_4w_beschreibung"] and k["kopie"]
    # Ähnlichkeit zum Titel ohne Vier-Wort-Folge
    assert W.kopierpruefung("Photosynthese", [], "Photosynthese!", "")["aehnlichkeit_titel"] > 0.8


def test_auswerten_und_vorschlag_nur_aus_vokabular():
    g = _gruppe("g", [], [])
    d = {"thema": " Addition   gleichnamiger Brüche ", "thema_allgemeiner": "null", "fachbegriffe": ["Bruch", 3, ""],
         "entfernt": ["Plattform", "verband"],
         "fach_vorschlag": {"label": "Mathematik", "uri": f"{DISC}380"},
         "stufe_vorschlag": {"label": "Primarstufe", "uri": f"{CTX}grundschule"}}
    e = W.auswerten(d, g, "vorschlag")
    assert e["thema"] == "Addition gleichnamiger Brüche" and e["thema_allgemeiner"] is None
    assert e["fachbegriffe"] == ["Bruch"] and e["entfernt"] == ["plattform", "sonstige"]
    assert e["vorschlag"] and e["fach"] == [{"uri": f"{DISC}380", "label": "Mathematik"}]
    assert e["stufe"][0]["uri"] == f"{CTX}grundschule"
    # erfundenes Label oder andere Option: kein Vorschlag
    d["fach_vorschlag"] = {"label": "Mathe", "uri": f"{DISC}380"}
    assert W.auswerten(d, g, "vorschlag")["fach"] == []
    assert not W.auswerten(d, g, "nur-thema")["vorschlag"]
    with pytest.raises(ValueError):
        W.auswerten({"thema": ""}, g, "weglassen")


def test_vokabular_im_festen_prompt_anfang():
    s_v, u = W.anfrage(_gruppe("g", [], []), "vorschlag")
    s_n, _ = W.anfrage(_gruppe("g", [f"{DISC}380"], [f"{CTX}grundschule"]), "vorschlag")
    assert s_v.startswith(s_n) and "SCHULFÄCHER" in s_v and f"{DISC}380" in s_v and "SCHULFÄCHER" not in s_n
    assert json.loads(u)["fach"] is None
    _, u2 = W.anfrage(_gruppe("g", [], []), "vorschlag", vorige="X")
    assert u2.startswith(u) and "„X“" in u2


# --- Batch-Lauf ----------------------------------------------------------------------------------------

def test_batch_lauf_kopie_wird_einmal_neu_gefragt(tmp_path):
    gruppen = {
        "a": _gruppe("a", [f"{DISC}380"], [f"{CTX}grundschule"], "Addition gleichnamiger Brüche im Alltag"),
        "b": _gruppe("b", [f"{DISC}380"], [f"{CTX}sekundarstufe_1"], "Lineare Funktionen zeichnen"),
        "c": _gruppe("c", [f"{DISC}380"], [f"{CTX}sekundarstufe_1"], "Satz des Pythagoras beweisen und anwenden"),
    }

    def antwort(user):
        d = json.loads(user.split("\n\nHinweis")[0])
        if d["titel"].startswith("Addition"):  # erst Kopie, nach dem Hinweis eigenständig
            t = "Rechnen mit Brüchen" if "Hinweis" in user else "Addition gleichnamiger Brüche im Alltag"
        elif d["titel"].startswith("Satz"):    # bleibt Kopie
            t = "Satz des Pythagoras beweisen und anwenden"
        else:
            t = "Graphen linearer Zuordnungen"
        return json.dumps({"thema": t, "thema_allgemeiner": "Algebra", "fachbegriffe": ["Funktion"], "entfernt": []})

    fake = FakeOpenAI(antwort)
    out = tmp_path / "abstraktion.jsonl"
    lauf = W.WloThemenLauf(gruppen, LLMConfig(api_key="x"), out, client=fake, vergleich=True)
    st = lauf.weiter()
    assert st["runde"] == 1 and st["fertig"] == 0
    st = lauf.weiter()  # Runde 1 abgeholt: b fertig, a und c neu gefragt
    assert st["fertig"] == 1 and st["runde"] == 2
    st = lauf.weiter()
    assert st["abgeschlossen"] and st["fertig"] == 3
    rows = {r["id"]: r for r in W.lade_jsonl(out)}
    assert rows["a"]["thema"] == "Rechnen mit Brüchen" and rows["a"]["seed_tauglich"] and rows["a"]["versuche"] == 2
    assert not rows["c"]["seed_tauglich"] and rows["c"]["pruefung"]["kopie"]
    assert rows["b"]["fach"][0]["label"] == "Mathematik" and rows["b"]["node_ids"] == ["b-n"]
    # keine Quelltexte in der Ausgabe, nur in der lokalen Prüfdatei
    text = out.read_text(encoding="utf-8")
    assert "Lineare Funktionen zeichnen" not in text and "Addition gleichnamiger Brüche im Alltag" not in text
    assert "Lineare Funktionen zeichnen" in lauf.vergleich_path.read_text(encoding="utf-8")
    assert st["tokens"]["eingabe"] == 500  # 5 Anfragen à 100
    kz = W.kennzahlen(out, lauf.stand, gnd={"funktion": {"uri": "u", "label": "Funktion"}})
    assert kz["gruppen"] == 3 and kz["kopie_nach_wiederholung"] == 1 and kz["wiederholt_wegen_kopie"] == 2
    assert kz["fachbegriffe_gnd_exakt"] == 3 and kz["tokens_je_gruppe"]["eingabe"] == pytest.approx(166.7)
    assert kz["kosten_usd"] == W.kosten(lauf.stand["tokens"], "gpt-6-luna") > 0
    assert kz["hochrechnung"]["gruppen"] == 3
    # fortsetzen: neuer Lauf auf derselben Ausgabe schickt nichts mehr
    lauf2 = W.WloThemenLauf(gruppen, LLMConfig(api_key="x"), out, client=fake)
    assert lauf2.weiter()["abgeschlossen"]
    with pytest.raises(ValueError):  # andere Einstellungen auf derselben Ausgabe
        W.WloThemenLauf(gruppen, LLMConfig(api_key="x"), out, client=fake, ohne_stufe="nur-thema")


class _FakeChat:
    """chat.completions.create für den Sofort-Modus; Flex-Anfragen werden protokolliert."""

    def __init__(self, antwort):
        self.antwort, self.tiers = antwort, []
        self.chat = type("C", (), {"completions": type("K", (), {"create": staticmethod(self._create)})()})()

    def _create(self, **params):
        from types import SimpleNamespace
        self.tiers.append(params.get("service_tier"))
        body = {"choices": [{"message": {"content": self.antwort(params["messages"][1]["content"])}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        return SimpleNamespace(model_dump=lambda: body)


@pytest.mark.parametrize("tier, faktor", [(None, 2), ("flex", 1)])
def test_sofort_ohne_batch(tmp_path, tier, faktor):
    gruppen = {"a": _gruppe("a", [f"{DISC}380"], [f"{CTX}grundschule"], "Zahlen bis zwanzig"),
               "b": _gruppe("b", [f"{DISC}380"], [f"{CTX}sekundarstufe_1"], "Lineare Funktionen zeichnen")}
    fake = _FakeChat(lambda u: json.dumps({"thema": "Rechnen mit natürlichen Zahlen", "thema_allgemeiner": "Arithmetik",
                                           "fachbegriffe": ["Addition"], "entfernt": []}))
    out = tmp_path / "abstraktion.jsonl"
    lauf = W.WloThemenLauf(gruppen, LLMConfig(api_key="x", service_tier=tier), out, client=fake, sofort=True)
    st = lauf.weiter()  # schickt und beantwortet sofort
    st = lauf.weiter()  # wertet aus
    assert st["abgeschlossen"] and st["fertig"] == 2 and fake.tiers == [tier, tier]
    assert lauf.stand["batches"][0]["id"].startswith("sofort:")
    assert st["kosten_usd"] == pytest.approx(W.kosten(lauf.stand["tokens"], "gpt-6-luna") * faktor)
    kz = W.kennzahlen(out, lauf.stand, gnd={})
    assert kz["hochrechnung"]["kosten_usd"] == pytest.approx(W.kosten(lauf.stand["tokens"], "gpt-6-luna"), abs=0.01)


def test_kosten():
    t = {"eingabe": 2_000_000, "davon_cache": 1_000_000, "ausgabe": 1_000_000}
    assert W.kosten(t, "gpt-6-luna") == pytest.approx(0.05 + 0.005 + 0.25)
    assert W.kosten(t, "unbekannt") is None


# --- Mapping auf Seeds ---------------------------------------------------------------------------------

def _abstraktion(gid, thema, fach, stufen, nodes, fb=("Bruch",), tauglich=True, vorschlag=False):
    return {"id": gid, "node_ids": nodes, "thema": thema, "thema_allgemeiner": "Bruchrechnung",
            "fachbegriffe": list(fb), "entfernt": [], "fach": [W.ref(u) for u in fach],
            "stufe": [W.ref(u) for u in stufen], "vorschlag": vorschlag, "seed_tauglich": tauglich,
            "pruefung": {"kopie": not tauglich}}


ROWS = [
    _abstraktion("g1", "Addition gleichnamiger Brüche", [f"{DISC}380"], [f"{CTX}grundschule"], ["n1", "n2"]),
    _abstraktion("g2", "addition gleichnamiger Brüche", [f"{DISC}380"], [f"{CTX}sekundarstufe_1"], ["n3"]),
    _abstraktion("g3", "Addition gleichnamiger Brüche", [f"{DISC}100"], [f"{CTX}grundschule"], ["n4"]),
    _abstraktion("g4", "Kopiertes Thema", [f"{DISC}380"], [f"{CTX}grundschule"], ["n5"], tauglich=False),
    _abstraktion("g5", "Lineare Algebra", [f"{HFS}n37", f"{DISC}380"], [f"{CTX}hochschule"], ["n6"],
                 fb=("Vektorraum",)),
    _abstraktion("g6", "Pflegeplanung", [f"{DISC}oeh04010"], [f"{CTX}berufliche_bildung"], ["n7"]),
    _abstraktion("g7", "Bruchrechnen mit Alltagsbezug", [f"{DISC}380"], [f"{CTX}schule"], ["n8"]),
]


def test_zusammenfassen_verknuepft_dubletten_statt_zu_loeschen():
    gruppen, z = WS.zusammenfassen(ROWS)
    schule = gruppen["schule"]
    assert len(schule) == 2  # g1+g2 (gleiches Thema, gleiches Fach) und g3 (anderes Fach)
    mathe = next(g for g in schule if len(g["node_ids"]) == 3)
    assert mathe["node_ids"] == ["n1", "n2", "n3"] and mathe["stufen"] == ["primar", "sek1"]
    assert len(gruppen["hochschule"]) == 1 and gruppen["hochschule"][0]["faecher"] == [f"{HFS}n37"]
    assert z["nicht_seed_tauglich"] == 1 and z["nicht_gemappt_berufsbildung"] == 1
    assert z["ohne_bereich_oder_fach"] == 1  # g7: nur „Schule“ ohne Schulstufe


class _FakeEmbedder:
    model = "fake"

    def encode(self, texts, art="ziel", on_progress=None):
        def v(t):
            t = t.casefold()
            x = np.array([("bruch" in t or "brüche" in t) * 1.0, ("linear" in t) * 1.0, 0.1 + ("alltag" in t)])
            return x / np.linalg.norm(x)
        return np.stack([v(t) for t in texts])


def test_bauen_mit_gnd_und_verknuepfung(ohne_lokal):
    p = ohne_lokal / "wlo_abstraktion" / "abstraktion.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8")
    gnd = {"bruch": {"uri": "https://d-nb.info/gnd/4008131-x", "label": "Bruch"}}
    vorhanden = {"schule": [{"id": "oeh:1", "label_de": "Addition gleichnamiger Brüche", "faecher": [f"{DISC}380"],
                             "herkunft": "themenbaum"},
                            {"id": "oeh:2", "label_de": "Bruchrechnung im Alltag", "faecher": [f"{DISC}380"]},
                            {"id": "oeh:3", "label_de": "Brüche", "faecher": [f"{DISC}240"]}],
                 "hochschule": []}
    st = WS.bauen([p], embedder=_FakeEmbedder(), gnd=gnd, vorhanden=vorhanden, on_progress=lambda *_: None)
    assert st["bereiche"]["schule"]["themen"] == 2 and st["bereiche"]["hochschule"]["themen"] == 1
    schule = WS.themen("schule")
    t = next(x for x in schule if x["wlo"]["gewicht"] == 3)
    assert t["id"].startswith(WLO_THEMA_PREFIX + "schule:") and t["herkunft"] == "wlo_abstraktion"
    assert t["label_de"] == "Addition gleichnamiger Brüche" and t["stufen"] == ["primar", "sek1"]
    assert t["fachbegriffe"][0]["gnd"]["uri"].endswith("4008131-x") and t["portal"] == "Mathematik"
    ziele = {x["id"]: x for x in t["verknuepft_mit"]}
    assert ziele["oeh:1"]["aehnlichkeit"] == 1.0 and ziele["oeh:1"]["art"] == "vorhanden"
    assert "oeh:3" not in ziele  # anderes Fach
    assert t["mapping"]["status"] == "gesichert"
    h = WS.themen("hochschule")[0]
    assert h["studienfach"][0]["uri"] == f"{HFS}n37" and h["art"] == "wlo_abstraktion"
    text = "".join(Path(ohne_lokal / "wlo_abstraktion" / n).read_text(encoding="utf-8")
                   for n in ("themen_schule.jsonl", "themen_hochschule.jsonl"))
    assert "n1" in text and "titel" not in text


def test_seeds_herkunft_und_anteil(ohne_lokal):
    b = get("schule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Schul-Themen gebaut")
    p = ohne_lokal / "wlo_abstraktion" / "abstraktion.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8")
    WS.bauen([p], gnd={}, vorhanden={"schule": [], "hochschule": []}, on_progress=lambda *_: None)
    b = get("schule")
    assert len([t for t in b.topics() if t["id"].startswith(WLO_THEMA_PREFIX)]) == 2
    # Standard: aus
    assert not [s for s in build_seeds(b, 50, seed=1) if s["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)]
    an = {"herkunft": ["themenbaum", "wlo_keyword", "wlo_abstraktion"], "wlo_anteil": ["10", "30"]}
    seeds = build_seeds(b, 100, seed=1, auswahl=an)
    wlo = [s for s in seeds if s["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)]
    assert len(wlo) == 30  # größter angehakter Wert
    s = wlo[0]
    assert s["ccm:taxonid"] and s["ccm:educationalcontext"]
    belege = s["synth:provenance"]["synth:thema"]["belege"]
    assert "wlo" in belege["erfuellt"] and belege["wlo"]["nodes"][0].startswith("https://")
    assert "Fach der WLO-Datensätze" in s["synth:provenance"]["ccm:taxonid"]["methode"]
    # nur WLO-Abstraktion: alle Seeds daraus
    nur = build_seeds(b, 10, seed=1, auswahl={"herkunft": ["wlo_abstraktion"]})
    assert all(x["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX) for x in nur)
    # Hochschule
    h = get("hochschule")
    try:
        h.topics()
    except FileNotFoundError:
        return
    hs = build_seeds(h, 40, seed=1, auswahl={"quellen": ["openalex", "wlo_abstraktion"], "wlo_anteil": ["50"]})
    hw = [x for x in hs if x["synth:thema"]["id"].startswith(WLO_THEMA_PREFIX)]
    assert len(hw) == 20 and hw[0]["ccm:oeh_taxonid_university"] == [f"{HFS}n37"]


def test_maskiert_vergleicht_vorschlaege_und_ist_nie_seed(tmp_path):
    gruppen = {"a": _gruppe("a", [f"{DISC}380"], [f"{CTX}grundschule"], "Brüche"),
               "b": _gruppe("b", [f"{DISC}100"], [f"{CTX}sekundarstufe_1"], "Säuren")}

    def antwort(user):
        assert json.loads(user)["fach"] is None and json.loads(user)["bildungsbereich"] is None
        return json.dumps({"thema": "Rechnen mit Brüchen", "fachbegriffe": [], "entfernt": [],
                           "fach_vorschlag": {"label": "Mathematik", "uri": f"{DISC}380"},
                           "stufe_vorschlag": {"label": "Primarstufe", "uri": f"{CTX}grundschule"}})

    out = tmp_path / "m.jsonl"
    lauf = W.WloThemenLauf(gruppen, LLMConfig(api_key="x"), out, client=FakeOpenAI(antwort), pool="maskiert")
    lauf.weiter()
    assert lauf.weiter()["abgeschlossen"]
    rows = W.lade_jsonl(out)
    assert all(r["maskiert"] and not r["seed_tauglich"] and r["vorschlag"] for r in rows)
    kz = W.vorschlag_kennzahlen(rows)["maskiert"]
    assert kz["gruppen"] == 2 and kz["fach_treffer"] == 1 and kz["stufe_treffer"] == 1
    with pytest.raises(ValueError):
        W.WloThemenLauf(gruppen, LLMConfig(api_key="x"), tmp_path / "x.jsonl", client=FakeOpenAI(antwort),
                        pool="maskiert", ohne_stufe="nur-thema")


def test_vorschlag_ungueltig_wird_gezaehlt():
    e = W.auswerten({"thema": "Thema", "fach_vorschlag": {"label": "X", "uri": "y"}}, _gruppe("g", [], []), "vorschlag")
    assert not e["vorschlag"] and e["vorschlag_ungueltig"] == ["fach", "stufe"]


def test_filter_wlo_vorschlag():
    from sem.bereiche.base import wlo_passt

    t = {"id": WLO_THEMA_PREFIX + "schule:x", "wlo": {"vorschlag": True}}
    assert wlo_passt(t, {"wlo_vorschlag": ["metadaten", "vorschlag"]})
    assert not wlo_passt(t, {"wlo_vorschlag": ["metadaten"]})
    assert wlo_passt({"id": "oeh:1"}, {"wlo_vorschlag": []})
    assert "wlo_vorschlag" in get("schule").auswahl_optionen() and "wlo_vorschlag" in get("hochschule").auswahl_optionen()


def test_teile_wlo():
    wlo = [{"id": WLO_THEMA_PREFIX + "x"}]
    rest = [{"id": "a"}, {"id": "b"}]
    assert _teile_wlo(rest + wlo, 10, None) == [(10, rest + wlo)]
    assert _teile_wlo(rest, 10, 0.5) == [(10, rest)]
    assert _teile_wlo(rest + wlo, 10, 0.2) == [(8, rest), (2, wlo)]

