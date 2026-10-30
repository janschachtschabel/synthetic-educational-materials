import pytest

from sem.quellen.wlo import datensaetze


def _export(tmp_path, kopf, *zeilen):
    p = tmp_path / "export.csv"
    p.write_text("\n".join([";".join(kopf), *(";".join(z) for z in zeilen)]) + "\n", encoding="utf-8-sig")
    return p


def test_datensaetze_liest_nur_gewuenschte_spalten(tmp_path):
    p = _export(tmp_path, ["sys:node-uuid", "x.ccm:educationalcontext_DISPLAYNAME", "cclom:general_keyword", "cclom:title"],
                ["n1", "Elementarbereich, Grundschule", " Farben ,Formen,", "geheim"])
    assert list(datensaetze([p], ["kontext", "keywords", "curriculum"])) == [
        ("n1", {"kontext": ["Elementarbereich", "Grundschule"], "keywords": ["Farben", "Formen"], "curriculum": []})]


def test_ohne_node_spalte_interner_schluessel_und_pflichtspalten(tmp_path):
    p = _export(tmp_path, ["cclom:general_keyword"], ["A"], ["B"])
    assert [n for n, _ in datensaetze([p], ["keywords"])] == ["#export.csv:0", "#export.csv:1"]
    with pytest.raises(KeyError, match="educationalcontext"):
        list(datensaetze([p], ["kontext", "keywords"], pflicht=("kontext",)))


def test_client_fehler_werden_nicht_wiederholt(monkeypatch):
    import urllib.error

    from sem.quellen import esco, wiederholen

    def fehler(code):
        return urllib.error.HTTPError("http://x", code, "x", {}, None)

    assert not wiederholen(fehler(404)) and wiederholen(fehler(429)) and wiederholen(fehler(503))
    assert wiederholen(TimeoutError())
    aufrufe = []

    def urlopen(req, timeout):
        aufrufe.append(1)
        raise fehler(400)

    monkeypatch.setattr(esco.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(esco.time, "sleep", lambda s: None)
    with pytest.raises(urllib.error.HTTPError):
        esco._get("/resource/occupation")
    assert len(aufrufe) == 1


def test_embedding_cache_einmal_am_ende_gespeichert(tmp_path, monkeypatch):
    import numpy as np

    from sem.mapping.embeddings import Embedder

    emb = Embedder("test", backend=lambda t: np.ones((len(t), 3)), cache_dir=tmp_path, batch=1)
    gespeichert = []
    original = emb._save
    monkeypatch.setattr(emb, "_save", lambda: (gespeichert.append(1), original()))
    v = emb.encode([f"text {i}" for i in range(20)])
    assert v.shape == (20, 3) and len(gespeichert) == 1
    assert Embedder("test", backend=lambda t: 1 / 0, cache_dir=tmp_path).encode(["text 3"]).shape == (1, 3)
