import json

from sem import entitaeten_ergaenzen as EE


def _rec(text, namen, ents=None):
    return {"id": "x", "synth:volltext": text, "synth:stil": {"namen": namen}, "synth:entitaeten": ents or [],
            "synth:generierung": {"modell": "m"}}


def test_fehlende_namen_werden_als_fiktive_person_ergaenzt():
    r = _rec("Lea fragt Herr Lehmann. Später antwortet Lehmann. Ida fehlt.", ["Lea", "Herr Lehmann", "Ida", "Tom"],
             [{"name": "Ida", "typ": "Person", "art": "Eigenname", "im_text": "Ida", "fiktiv": True, "positionen": []}])
    assert EE.ergaenzen_rec(r) == 2
    ents = {e["name"]: e for e in r["synth:entitaeten"]}
    assert set(ents) == {"Ida", "Lea", "Herr Lehmann"}
    assert ents["Lea"] == {"name": "Lea", "typ": "Person", "kategorie": "Personen & Gruppen", "art": "Eigenname",
                           "im_text": "Lea", "fiktiv": True, "positionen": [[0, 3]]}
    assert ents["Herr Lehmann"]["positionen"] == [[10, 22]]
    assert r["synth:generierung"] == {"modell": "m", "entitaeten_ergaenzt": 2}


def test_nachname_reicht_und_ganzes_wort():
    r = _rec("Yılmaz erklärt es. Leander kommt.", ["Frau Yılmaz", "Lea"])
    assert EE.ergaenzen_rec(r) == 1
    (e,) = r["synth:entitaeten"]
    assert e["name"] == "Frau Yılmaz" and e["im_text"] == "Yılmaz" and e["positionen"] == [[0, 6]]
    # vorhandene Entität mit Nachnamen gilt als vorhanden
    r = _rec("Frau Nowak lacht.", ["Frau Nowak"], [{"name": "Nowak", "art": "Eigenname", "im_text": "Nowak"}])
    assert EE.ergaenzen_rec(r) == 0 and "entitaeten_ergaenzt" not in r["synth:generierung"]


def test_reihenfolge_und_hoechstzahl():
    begriffe = [{"name": f"B{i}", "art": "Begriff", "im_text": f"B{i}"} for i in range(30)]
    r = _rec("Lea rechnet.", ["Lea"], begriffe)
    assert EE.ergaenzen_rec(r) == 1
    assert len(r["synth:entitaeten"]) == 30 and r["synth:entitaeten"][0]["name"] == "Lea"


def test_datei(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps(_rec("Lea liest.", ["Lea"])) + "\n" + json.dumps(_rec("Text.", ["Lea"])) + "\n",
                 encoding="utf-8")
    z = EE.ergaenzen(p, EE.standard_ausgabe(p))
    assert z == {"materialien": 2, "materialien_ergaenzt": 1, "namen_ergaenzt": 1, "vorher_ohne_entitaeten": 1}
    assert EE.standard_ausgabe(p).name == "m_entitaeten.jsonl"


def test_suchanfragen_schule(tmp_path):
    from sem import korrektur as K

    q = ["bruchrechnung klasse 6", "brüche", "bruch kürzen", "brüche grundschule", "bruchrechnung oberstufe", "anteile"]
    assert K.suchanfragen_kuerzen(q, K.STUFE_SCHULE) == ["bruchrechnung klasse 6", "brüche", "bruch kürzen", "anteile"]
    p = tmp_path / "m.jsonl"
    p.write_text(json.dumps({"id": "a", "synth:bildungsbereich": "schule", "synth:suchanfragen": q}) + "\n"
                 + json.dumps({"id": "b", "synth:bildungsbereich": "elementar", "synth:suchanfragen": q}) + "\n",
                 encoding="utf-8")
    z = K.suchanfragen_datei(p, tmp_path / "o.jsonl")
    assert z == {"materialien": 2, "suchanfragen_gekuerzt": 1, "suchanfragen_gestrichen": 2}
    a, b = (json.loads(x) for x in (tmp_path / "o.jsonl").read_text(encoding="utf-8").splitlines())
    assert len(a["synth:suchanfragen"]) == 4 and a["synth:suchanfragen_alt"] == q and "synth:suchanfragen_alt" not in b
