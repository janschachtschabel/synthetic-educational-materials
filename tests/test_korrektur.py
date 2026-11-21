"""Gezielte Korrekturen: Auswahl, nur fehlende Anfragen im Batch-Lauf, Zusammenführen mit *_alt-Feldern."""

import functools
import json

from sem import korrektur as K
from sem import schema as S
from sem.batch import BatchLauf
from sem.llm import LLMConfig
from sem.prompts import load_prompts

from test_batch import Antworten, FakeOpenAI, _zeilen, seeds_datei  # noqa: F401


def test_suchanfragen_kuerzen():
    q = ["kernphysik", "kernphysik hochschule", "atomkern studium", "zerfall", "bindungsenergie", "isotope"]
    assert K.suchanfragen_kuerzen(q) == ["kernphysik", "kernphysik hochschule", "zerfall", "bindungsenergie", "isotope"]
    # bleiben weniger als 4, wird nichts gestrichen
    kurz = ["a hochschule", "b studium", "c uni", "d"]
    assert K.suchanfragen_kuerzen(kurz) == kurz
    assert K.suchanfragen_kuerzen(["bachelorarbeit schreiben", "x", "y", "z"]) == ["bachelorarbeit schreiben", "x", "y", "z"]


def test_fiktive_fragen():
    rec = {S.F_STIL: {"namen": ["Emma", "Jonas", "Herr Weber"]}, S.F_QA: [
        {"frage": "Was rät Herr Weber?"}, {"frage": "Warum zögert Emma?"},
        {"frage": "Welche Herrschaftstypen unterscheidet Max Weber?"}, {"frage": "Was ist Entropie?"}]}
    assert K.fiktive_fragen(rec) == 2


def _laufen(lauf):
    for _ in range(12):
        if lauf.weiter()["abgeschlossen"]:
            return
    raise AssertionError("Lauf nicht fertig")


def test_vorbereiten_lauf_zusammenfuehren(seeds_datei, tmp_path, monkeypatch):  # noqa: F811
    path, seeds = seeds_datei
    prompts = load_prompts("hochschule")
    cfg = LLMConfig(api_key="x", model="m")
    lauf = BatchLauf(path, tmp_path / "materialien_t.jsonl", cfg, client=FakeOpenAI(Antworten(prompts, titel_kaputt=0)))
    _laufen(lauf)
    recs = _zeilen(tmp_path / "materialien_t.jsonl")
    a, b, c = recs
    # a: RVK-Thema mit Klammerzusatz → ganz neu
    a[S.F_THEMA] = {**a[S.F_THEMA], "rvk": {"notation": "UO 4000"}, "label_de": "Kernphysik (ohne Spaltung und Streuung)"}
    a[S.F_FULLTEXT] = "ALTER TEXT"
    # b: E-Book in der Beschreibung und Figurenfragen → beide Felder neu
    b[S.F_DESCRIPTION] = "Ein Skript (auch als E-Book) über Eigenwerte."
    b[S.F_STIL] = {"namen": ["Emma", "Jonas", "Frau Krüger"]}
    b[S.F_QA] = [{"frage": f"Was sagt Frau Krüger zu Punkt {i}?", "antwort": "A.", "typ": "erinnern"} for i in range(3)]
    b[S.F_SUCHANFRAGEN] = ["eigenwerte", "eigenwerte hochschule", "eigenwerte studium", "matrix", "spektralsatz"]
    # c: nichts zu korrigieren
    eingabe = tmp_path / "materialien_hs.jsonl"
    eingabe.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    eingabe_vorher = eingabe.read_bytes()

    monkeypatch.setattr(K, "ausgabe_pfad", functools.partial(K.ausgabe_pfad, ordner=tmp_path))
    z = K.vorbereiten(eingabe, "korr")
    assert z["voll"] == 1 and z["beschreibung"] == 1 and z["fragen_antworten"] == 1
    assert z["suchanfragen_gekuerzt"] == 1
    p = K.pfade("korr")
    assert p["seeds"] == tmp_path / "seeds_korr.jsonl"
    assert [r["id"] for r in _zeilen(p["seeds"])] == [a["id"], b["id"]]

    client = FakeOpenAI(Antworten(prompts, titel_kaputt=0))
    _laufen(BatchLauf(p["seeds"], p["ergebnis"], cfg, client=client))
    gefragt = [(r["custom_id"].split("|")[0], r["custom_id"].split("|")[1]) for r in client.anfragen]
    # b: nur Beschreibung und Frage-Antwort-Paare (einzeln), a: ganz neu (Volltext, Metadaten kombiniert, Inhaltstyp)
    assert sorted(f for i, f in gefragt if i == b["id"]) == ["beschreibung", "fragen_antworten"]
    assert sorted(f for i, f in gefragt if i == a["id"]) == ["inhaltstyp", "metadaten", "volltext"]
    abgrenzung = [r["body"]["messages"][1]["content"] for r in client.anfragen
                  if r["custom_id"].startswith(a["id"] + "|volltext")][0]
    assert "Abgrenzung des Themas" in abgrenzung and "ohne Spaltung und Streuung" in abgrenzung

    out = tmp_path / "materialien_hs_korrigiert.jsonl"
    z = K.zusammenfuehren(eingabe, out, "korr")
    assert z == {"materialien": 3, "korrigiert_voll": 1, "korrigiert_beschreibung+fragen_antworten": 1,
                 "suchanfragen_gekuerzt": 1, "suchanfragen_gestrichen": 1}
    assert eingabe.read_bytes() == eingabe_vorher
    na, nb, nc = _zeilen(out)
    assert na[S.F_FULLTEXT] != "ALTER TEXT" and na[S.F_GENERATION]["korrektur"]["neu_erzeugt"]
    assert nb[S.F_DESCRIPTION] != b[S.F_DESCRIPTION] and nb[K.F_BESCHREIBUNG_ALT] == b[S.F_DESCRIPTION]
    assert nb[S.F_QA] != b[S.F_QA] and nb[K.F_QA_ALT] == b[S.F_QA]
    assert nb[S.F_FULLTEXT] == b[S.F_FULLTEXT] and nb[S.F_TITLE] == b[S.F_TITLE] and nb[S.F_KEYWORD] == b[S.F_KEYWORD]
    assert nb[S.F_SUCHANFRAGEN] == ["eigenwerte", "eigenwerte hochschule", "matrix", "spektralsatz"]
    assert nb[K.F_SUCHANFRAGEN_ALT] == b[S.F_SUCHANFRAGEN]
    assert nb[S.F_GENERATION]["korrektur"]["felder"] == ["beschreibung", "fragen_antworten"]
    assert nb[S.F_GENERATION]["modell"] == b[S.F_GENERATION]["modell"]
    assert nc == c
    # ein zweiter Start unter demselben Namen wird abgelehnt
    try:
        K.vorbereiten(eingabe, "korr")
    except ValueError:
        pass
    else:
        raise AssertionError("zweiter Start nicht abgelehnt")
