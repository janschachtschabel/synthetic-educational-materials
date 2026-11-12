import pytest

from sem import gnd_ergaenzung as ge
from sem.quellen import gnd


def _m(uri, name, typ="SubjectHeadingSensoStricto", definition=None):
    return {"id": uri, "gndIdentifier": uri.rsplit("/", 1)[-1], "preferredName": name,
            "type": ["AuthorityResource", typ], **({"definition": [definition]} if definition else {})}


def test_motiv_ersatz_und_sortierung(monkeypatch):
    antwort = {"member": [_m("https://d-nb.info/gnd/1", "Amsterdam <Motiv>"),
                          _m("https://d-nb.info/gnd/2", "Amsterdam", "PlaceOrGeographicName"),
                          _m("https://d-nb.info/gnd/3", "Amsterdam, Hans", "DifferentiatedPerson")]}
    monkeypatch.setattr(gnd, "_get", lambda base, params, **kw: antwort)
    assert gnd.ist_motiv("Eiffelturm <Paris, Motiv>") and not gnd.ist_motiv("Motivation")
    assert gnd.motiv_ersatz("Amsterdam <Motiv>")["uri"] == "https://d-nb.info/gnd/2"
    # exakte Suche: ein Motiv-Satz verliert gegen jeden anderen Treffer
    antwort = {"member": [_m("https://d-nb.info/gnd/1", "Mais <Motiv>"), _m("https://d-nb.info/gnd/4", "Mais")]}
    assert gnd.suche_exakt("Mais")["uri"] == "https://d-nb.info/gnd/4"
    antwort = {"member": [_m("https://d-nb.info/gnd/3", "Mais, Karl", "DifferentiatedPerson")]}
    assert gnd.motiv_ersatz("Mais <Motiv>") is None


def test_anwenden():
    erg = {"https://d-nb.info/gnd/1": {"uri": "https://d-nb.info/gnd/1", "motiv_ersatz":
                                       {"uri": "https://d-nb.info/gnd/2", "label": "Amsterdam"},
                                       "beschreibung": "Hauptstadt der Niederlande",
                                       "beschreibung_quelle": {"name": "Wikidata, Beschreibung"}},
           "https://d-nb.info/gnd/5": {"uri": "https://d-nb.info/gnd/5", "beschreibung": "neu",
                                       "beschreibung_quelle": {"name": "GND"}}}
    topics = [{"id": "https://d-nb.info/gnd/1", "label_de": "Amsterdam", "label_gnd": "Amsterdam <Motiv>"},
              {"id": "http://w3id.org/openeduhub/vocabs/oeh-topics/x", "label_de": "Algen",
               "gnd": {"uri": "https://d-nb.info/gnd/1", "label": "Algen <Motiv>"}, "definition": "alt"},
              {"id": "https://d-nb.info/gnd/5", "label_de": "Lupe", "definition": ""},
              {"id": "https://d-nb.info/gnd/9", "label_de": "unberührt"}]
    out = ge.anwenden(topics, "schule", erg)
    assert out[0]["id"] == "https://d-nb.info/gnd/2" and out[0]["label_gnd"] == "Amsterdam"
    assert out[0]["gnd_motiv"] == "https://d-nb.info/gnd/1" and out[0]["definition"] == "Hauptstadt der Niederlande"
    assert out[1]["id"].endswith("/x") and out[1]["gnd"]["uri"] == "https://d-nb.info/gnd/2"
    assert out[1]["definition"] == "alt"  # vorhandene Beschreibung bleibt
    assert out[2]["definition"] == "neu" and out[2]["beschreibung_quelle"] == {"name": "GND"}
    assert out[3] is topics[3] and topics[0]["id"] == "https://d-nb.info/gnd/1"  # Eingabe unverändert
    assert ge.anwenden(topics, "hochschule", {"https://d-nb.info/gnd/5": erg["https://d-nb.info/gnd/5"]})[2][
        "beschreibung_de"] == "neu"


def test_sammeln_findet_motive():
    try:
        k = ge.sammeln()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    motive = [v for v in k.values() if v["motiv"]]
    assert motive and all("Motiv>" in v["label"] for v in motive)
