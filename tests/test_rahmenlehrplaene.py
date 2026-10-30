from sem.quellen import rahmenlehrplaene as R

MD = """## Teil V Lernfelder
## Übersicht über die Lernfelder für den Ausbildungsberuf Beispielkaufmann

| Lernfelder | Lernfelder | Zeitrichtwerte | Zeitrichtwerte |
|---|---|---|---|
| Nr. | | 1. Jahr | 2. Jahr |
| 1 | Die eigene Rolle im Be- trieb mitgestalten | 80 | |
| 2 | Aufträge bearbeiten | | 60 |
| | 10  Gesamtwirtschaftliche Einflüsse analysieren | | 40 |

Lernfeld 1:
Die eigene Rolle im Betrieb mitgestalten
1. Ausbildungsjahr Zeitrichtwert: 80 Stunden
Die Schülerinnen und Schüler verfügen über die Kompetenz, im Lernfeld 2 weiterzuarbeiten.

## Lernfeld 2: Aufträge bearbeiten
2. Ausbildungsjahr
Zeitrichtwert: 60 Std.

Lernfeld 10 Gesamtwirtschaftliche Einflüsse analysieren
2. Ausbildungsjahr Zeitrichtwert: 40 Stunden
"""


def test_tabelle_mit_verrutschter_nummer():
    t = R.tabelle(MD)
    assert [x["nr"] for x in t] == ["1", "2", "10"]
    assert t[0]["titel"] == "Die eigene Rolle im Betrieb mitgestalten"
    assert t[1]["stunden"] == {2: 60}


def test_kopfzeilen_ohne_verweise_im_fliesstext():
    k = R.kopfzeilen(MD)
    assert [(x["nr"], x["jahr"], x["stunden"]) for x in k] == [("1", 1, 80), ("2", 2, 60), ("10", 2, 40)]


def test_befund_luecken():
    e = R.lernfelder(MD)
    assert e["befund"]["luecken"] == [3, 4, 5, 6, 7, 8, 9]
    assert e["befund"]["status"] == "unvollstaendig"
    assert all(f["in_tabelle"] and f["in_kopfzeilen"] for f in e["lernfelder"])


def test_lernfeld_text():
    from sem.bereiche.berufsbildung.lernfelder import lernfeld_text
    assert lernfeld_text({"nr": "9eg", "titel": "Titel", "jahr": 3, "stunden": 80}) == \
        "Lernfeld 9 eg: Titel (3. Ausbildungsjahr, 80 Stunden)"
