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
            wahl = codes[:1] if "Lehrebene" in system else codes[:2]
            res.append({"id": i["id"], "auswahl": [{"code": c, "begruendung": "passt",
                                                    "belegbegriffe": [i["label_en"].split()[0], "erfunden"]}
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
    prog = b.mapping_ausfuehren(b.mapping_folge, llm.LLMConfig(api_key="x", workers=2), on_progress=None)
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
