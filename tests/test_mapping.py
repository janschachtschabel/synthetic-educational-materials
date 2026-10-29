"""Mapping-Pipeline Hochschule mit Attrappen für LLM und Embeddings (ohne Netz)."""

import csv
import json
import zlib

import numpy as np
import pytest

from sem import llm
from sem.bereiche import get
from sem.bereiche.hochschule import DIR
from sem.bereiche.hochschule import build as B
from sem.mapping.abstimmung import auswerten
from sem.mapping.embeddings import Embedder


def fake_vectors(texts):
    out = np.zeros((len(texts), 64), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in t.lower().split():
            out[i, zlib.crc32(w.encode()) % 64] += 1
    return out


class FakeClient:
    def __init__(self, cfg):
        pass

    def chat_json(self, system, user):
        if "Studienfach der Destatis" in system:
            return {"beschreibung_de": "Ein Fach.", "typische_themen": ["A", "B"], "abgrenzung": ""}
        items = json.loads(user)
        if "übersetzt" in system:
            return {"ergebnisse": [{"id": i["id"], "label_de": i["label_en"] + " (de)", "beschreibung_de": "x",
                                    "keywords_de": ["Wort"]} for i in items]}
        res = []
        for i in items:
            codes = sorted(k.split(" | ")[0] for k in i["kandidaten"])
            if "Bildungsbereich" in system:
                wahl = ["kein_lehrthema" if i["label"] == "Video" else "hochschule"]
            else:
                wahl = codes[:1] if "Lehrebene" in system else codes[:2]
            name = i.get("label_en") or i.get("label") or i.get("studienfach")
            res.append({"id": i["id"], "auswahl": [{"code": c, "begruendung": "passt",
                                                    "belegbegriffe": [name.split()[0], "erfunden"]}
                                                   for c in wahl]})
        return {"ergebnisse": res}


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    with (DIR / "openalex_teilgebiet_studienbereich.csv").open(encoding="utf-8") as f:
        subfields = [r["openalex_subfield_id"] for r in csv.DictReader(f)][:6]
    topics = [{"id": f"T{100 + i}", "display_name": f"Topic{i} studies", "description": f"About topic {i}.",
               "keywords": [f"kw{i}", "science"], "subfield": {"id": subfields[i % 6], "display_name": "S"},
               "field": {"id": "1", "display_name": "F"}, "domain": {"id": "1", "display_name": "D"}}
              for i in range(12)]
    monkeypatch.setattr(B, "load_openalex_topics", lambda: topics)
    for name in ("THEMEN_PATH", "PROFILE_PATH", "PRUEFLISTE_PATH", "KORREKTUREN_PATH", "KANDIDATEN_PATH",
                 "STIMMEN_FAECHER", "STIMMEN_LEHREBENE", "UEBERSETZUNG_PATH"):
        monkeypatch.setattr(B, name, tmp_path / getattr(B, name).name)
    monkeypatch.setattr(B, "CACHE", tmp_path)
    monkeypatch.setattr(llm, "LLMClient", FakeClient)
    real_init = Embedder.__init__
    monkeypatch.setattr(Embedder, "__init__", lambda self, model="fake", **kw: real_init(
        self, model, backend=fake_vectors, cache_dir=tmp_path))
    return tmp_path


def test_pipeline_ergibt_belegte_zuordnungen(pipeline):
    b = get("hochschule")
    prog = b.mapping_ausfuehren(b.mapping_folgen["openalex"], llm.LLMConfig(api_key="x", workers=2), on_progress=None)
    assert "fertig" in prog.status
    rows = [json.loads(l) for l in B.THEMEN_PATH.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    for r in rows:
        m = r["mapping"]
        assert m["status"] == "gesichert" and m["konfidenz"] == 1.0
        assert len(r["studienfach"]) == 2 and r["lehrebene"] in B.LEHREBENEN
        f = m["faecher"][0]
        assert f["stimmen"] == 3 and f["aehnlichkeit"] is not None
        assert f["belegbegriffe_im_text"] == [r["label_en"].split()[0]]  # „erfunden“ steht nicht im Text
        assert r["label_de"].endswith("(de)")
    # Korrektur hat Vorrang und markiert die Zeile als redaktionell
    with B.KORREKTUREN_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "korrektur_codes", "korrektur_lehrebene", "kommentar"])
        w.writerow([rows[0]["id"], "079", "grundlagen", "Test"])
    B.zusammenfuehren(stimmen=3)
    r0 = json.loads(B.THEMEN_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert [f["notation"] for f in r0["studienfach"]] == ["079"]
    assert r0["lehrebene"] == "grundlagen" and r0["mapping"]["status"] == "redaktionell"


def test_auswerten_mehrheit_und_unsicher():
    w = lambda *c: [{"code": x, "begruendung": "", "belegbegriffe": []} for x in c]
    r = auswerten({0: w("a"), 1: w("a", "b"), 2: w("c")}, 3)
    assert r["auswahl"] == ["a"] and r["status"] == "wahrscheinlich" and r["verworfen"] == {"b": 1, "c": 1}
    r = auswerten({0: w("a"), 1: w("b"), 2: w("c")}, 3)
    assert r["status"] == "unsicher" and len(r["auswahl"]) == 1
    assert auswerten({0: w("a", "b"), 1: w("a"), 2: w("c", "a")}, 3)["status"] == "gesichert"
    assert auswerten({0: [], 1: [], 2: []}, 3)["status"] == "unsicher"


def test_thema_ohne_stimme_bleibt_und_fehlende_antwort_wird_nachgefragt(pipeline, monkeypatch):
    b = get("hochschule")
    cfg = llm.LLMConfig(api_key="x", workers=2)
    real = FakeClient.chat_json
    aufrufe = []

    def launisch(self, system, user):
        res = real(self, system, user)
        if not user.startswith("["):
            return res
        items = json.loads(user)
        aufrufe.append(len(items))
        if "Lehrebene" in system:
            if len(items) > 1:  # im Batch fehlt T100, einzeln kommt sie
                res["ergebnisse"] = [r for r in res["ergebnisse"] if not r["id"].endswith("T100")]
        elif "kandidaten" in user:
            for r in res["ergebnisse"]:
                if r["id"].endswith("T101"):
                    r["auswahl"] = []
        return res

    monkeypatch.setattr(FakeClient, "chat_json", launisch)
    b.mapping_ausfuehren(b.mapping_folgen["openalex"], cfg)
    b.mapping_ausfuehren(["lehrebene", "zusammenfuehren"], cfg)  # zweiter Lauf fragt T100 einzeln nach
    rows = {json.loads(l)["id"]: json.loads(l) for l in B.THEMEN_PATH.read_text(encoding="utf-8").splitlines()}
    assert len(rows) == 12
    r101 = rows["https://openalex.org/T101"]
    assert r101["mapping"]["status"] == "unsicher" and r101["mapping"]["ersatz"] and len(r101["studienfach"]) == 1
    assert sum(rows["https://openalex.org/T100"]["mapping"]["lehrebene"]["stimmen"].values()) == 3
    assert "T101" in B.PRUEFLISTE_PATH.read_text(encoding="utf-8")


def test_gnd_pipeline_nur_sachbegriffe_mit_belegen(pipeline, monkeypatch, tmp_path):
    from sem.bereiche.hochschule import gnd_themen as G
    from sem.quellen import gnd

    for name in ("WLO_PATH", "POOL_PATH", "STIMMEN_RELEVANZ", "STIMMEN_BRUECKE", "KANDIDATEN_GND",
                 "STIMMEN_GND_FAECHER", "STIMMEN_GND_LEHREBENE", "BRUECKE_PATH", "THEMEN_GND", "PRUEF_GND",
                 "AUSGESCHLOSSEN"):
        monkeypatch.setattr(G, name, tmp_path / getattr(G, name).name)
    sc = "https://d-nb.info/standards/vocab/gnd/gnd-sc#"
    vocab = {"scheme": {}, "concepts": [
        {"uri": sc + "21.2", "notation": "21.2", "label": "Physik", "broader": None, "level": 0, "art": "sache"},
        {"uri": sc + "13.5", "notation": "13.5", "label": "Medien", "broader": None, "level": 0, "art": "sache"},
        {"uri": sc + "28p", "notation": "28p", "label": "Personen", "broader": None, "level": 0, "art": "personen"}]}
    from sem import vocabs
    real_load = G.load_vocab
    monkeypatch.setattr(G, "load_vocab", lambda n: vocabs.Vocab(n, {}, vocab["concepts"]) if n == "gnd_sachgruppen"
                        else real_load(n))

    def rec(i, label, typ="SubjectHeadingSensoStricto", sg="21.2"):
        return {"uri": f"https://d-nb.info/gnd/{i}", "gnd_id": str(i), "label": label, "typen": [typ],
                "synonyme": [], "sachgruppen": [{"uri": sc + sg, "label": "x"}], "oberbegriffe": [], "definition": ""}
    recs = {r["uri"]: r for r in [rec(1, "Thermodynamik"), rec(2, "Video", sg="13.5"),
                                  rec(3, "Max Planck", typ="DifferentiatedPerson", sg="28p"), rec(4, "Optik")]}
    monkeypatch.setattr(gnd, "katalog_top", lambda p, a: [(u, 5) for u in recs if u != "https://d-nb.info/gnd/4"]
                        if p == "530" else [])
    monkeypatch.setattr(gnd, "hole", lambda uris: {u: recs[u] for u in uris if u in recs})
    monkeypatch.setattr(gnd, "suche_exakt", lambda kw: recs["https://d-nb.info/gnd/4"] if kw == "Optik" else None)
    monkeypatch.setattr(gnd, "katalog_zaehlen", lambda u: {"titel": 50, "lehrbuecher": 7, "hochschulschriften": 3})
    (tmp_path / "wlo_keywords_hochschule.json").write_text(json.dumps({"keywords": {
        "optik": {"schreibweisen": {"Optik": 4}, "anzahl": 4, "nodes": ["abc"],
                  "faecher": {"http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/n128": 4}},
        "lmu münchen": {"schreibweisen": {"LMU München": 9}, "anzahl": 9, "nodes": [], "faecher": {}}}}),
        encoding="utf-8")
    b = get("hochschule")
    cfg = llm.LLMConfig(api_key="x", workers=2)
    prog = b.mapping_ausfuehren(["fachprofile"] + b.mapping_folgen["gnd"][1:], cfg)
    assert "fertig" in prog.status, prog.status
    rows = [json.loads(l) for l in G.THEMEN_GND.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["label_de"] for r in rows) == ["Optik", "Thermodynamik"]  # Person raus, Video nicht Hochschule
    optik = next(r for r in rows if r["label_de"] == "Optik")
    assert optik["belege"]["wlo"]["datensaetze"] == 4 and optik["belege"]["wlo"]["nodes"][0].endswith("/abc")
    assert optik["belege"]["katalog"]["lehrbuecher"] == 7 and optik["quelle"]["lizenz"] == "CC0-1.0"
    assert optik["mapping"]["hochschulrelevanz"]["stimmen"]["hochschule"] == 3
    assert "Video" in G.AUSGESCHLOSSEN.read_text(encoding="utf-8")
    assert G.BRUECKE_PATH.exists()


def test_wlo_belege_an_openalex_themen(pipeline, monkeypatch, tmp_path):
    from sem.bereiche.hochschule import gnd_themen as G

    monkeypatch.setattr(G, "WLO_PATH", tmp_path / "wlo.json")
    b = get("hochschule")
    b.mapping_ausfuehren(b.mapping_folgen["openalex"], llm.LLMConfig(api_key="x", workers=2))
    r0 = json.loads(B.THEMEN_PATH.read_text(encoding="utf-8").splitlines()[0])
    fach = r0["studienfach"][0]["uri"]
    G.WLO_PATH.write_text(json.dumps({"keywords": {
        "kw0": {"schreibweisen": {"kw0": 3}, "anzahl": 3, "nodes": [], "faecher": {fach: 3}},
        "science": {"schreibweisen": {"science": 50}, "anzahl": 50, "nodes": [], "faecher": {fach: 50}}}}),
        encoding="utf-8")
    assert "1 OpenAlex-Themen mit WLO-Beleg, davon 1" in G.build_wlo_belege().status
    r0 = json.loads(B.THEMEN_PATH.read_text(encoding="utf-8").splitlines()[0])
    assert r0["belege"]["wlo"]["fach_passt"] and r0["belege"]["wlo"]["keywords"] == ["kw0"]  # „science“ zu unspezifisch
