"""RVK als Themenquelle der Hochschule: Auswahl, Pfade, Fachvererbung, Abgleich, Filter (ohne Netz und ohne Abzug)."""

import json

import pytest

from sem.bereiche import get
from sem.bereiche.hochschule import rvk_themen as RT
from sem.quellen import rvk as R

GEO = {"Bayern", "Frankreich"}


def klasse(nt, label, pfad=(), register=()):
    return {"notation": nt, "label": label, "tiefe": len(pfad) + 1,
            "pfad": [{"notation": n, "label": l} for n, l in pfad], "register": list(register)}


QP = [("Q", "Wirtschaftswissenschaften"), ("QC", "Volkswirtschaftstheorie")]


@pytest.mark.parametrize("k, erwartet", [
    (klasse("QC 300", "Konjunkturtheorie", QP), "thematisch"),
    (klasse("QC 010", "Zeitschriften", QP), "form"),
    (klasse("QC 020", "Allgemeines, Gesamtdarstellungen", QP), "form"),
    (klasse("QC 030", "Festschriften bei sachlichem Gesichtspunkt", QP), "form"),
    (klasse("PN 100", "Allgemeines Verwaltungsrecht", [("P", "Rechtswissenschaft"), ("PN", "Verwaltungsrecht")]),
     "thematisch"),
    (klasse("QC 040", "Frankreich", QP), "geografie"),
    (klasse("QC 041", "Agrarpolitik", QP + [("QC 040", "Bayern")]), "unter_geografie"),
    (klasse("QC 050", "19. Jahrhundert", QP), "zeit"),
    (klasse("QC 060", "Orte T", QP), "sammel"),
    (klasse("QC 061", "Pemfling", QP + [("QC 06", "Einzelne Orte der Oberpfalz")]), "instanz"),
    (klasse("GE 100", "Goethe, Johann Wolfgang von", [("G", "Germanistik"), ("GE", "Einzelne Autoren")]), "person"),
    (klasse("GE 101", "zum Gesamtwerk", [("G", "Germanistik")]), "werk"),
    (klasse("YB 100", "Therapie", [("X - Y", "Medizin"), ("YB", "B. Sachschlüssel")]), "schluessel"),
    (klasse("AA 100", "Bibliografien", [("A", "Allgemeines"), ("AA", "Bibliografien der Bibliografien")]),
     "ausgeschlossen"),
])
def test_typ(k, erwartet):
    assert R.typ(k, GEO) == erwartet


def test_label_unklar_und_uri():
    assert R.label_unklar("Theorie") and R.label_unklar("Methoden und Verfahren") and R.label_unklar("zum Werk")
    assert not R.label_unklar("Konjunkturtheorie")
    assert R.uri("QP 300 - QP 399") == "http://rvk.uni-regensburg.de/nt/QP_300_-_QP_399"
    assert R.url("ST 180").endswith("#notation/ST%20180")
    assert R.quartal("rvko_marcxml_2026_3.xml.gz") == "2026_3"


def test_auswahl_nur_thematisch_tiefe_2_bis_5():
    ks = [klasse("Q", "Wirtschaftswissenschaften"), klasse("QC", "Volkswirtschaftstheorie", QP[:1]),
          klasse("QC 300", "Konjunkturtheorie", QP, [{"tag": "750", "a": "Konjunktur", "0": "(DE-588)4032134-4"},
                                                     {"tag": "751", "a": "Bayern", "0": "(DE-588)4005044-2"}]),
          klasse("QC 010", "Zeitschriften", QP)]
    sel, stat = R.auswaehlen(ks)
    assert [s["notation"] for s in sel] == ["QC", "QC 300"]
    s = sel[1]
    assert s["pfad"] == ["Wirtschaftswissenschaften", "Volkswirtschaftstheorie"]
    assert s["gnd"] == [{"id": "4032134-4", "label": "Konjunktur"}]  # nur Sachschlagwörter
    assert stat["thematisch_je_tiefe"] == {2: 1, 3: 1}


@pytest.fixture
def rvk_dateien(tmp_path, monkeypatch):
    """Kleine RVK-Auswahl, Stimmen und Kandidaten im Zwischenspeicher; vorhandene Themen als Attrappe."""
    from sem.vocabs import load

    voc = load("hochschulfaechersystematik")
    fach = next(c for c in voc.concepts if c["level"] == 2)
    for name in ("KLASSEN", "AUSWAHL_INFO", "PRUEF_RVK", "AUSGESCHLOSSEN_RVK", "KANDIDATEN_RVK", "STIMMEN_RVK_FAECHER",
                 "STIMMEN_RVK_LEHREBENE", "LABELS_RVK", "THEMEN_RVK", "BELEGE_RVK", "THEMEN_RVK_REPO"):
        monkeypatch.setattr(RT, name, tmp_path / getattr(RT, name).name)
    monkeypatch.setattr(RT.B, "KORREKTUREN_PATH", tmp_path / "korrekturen.csv")
    zeilen = [
        {"notation": "Q", "label": "Wirtschaftswissenschaften", "tiefe": 1, "ober": None, "thema": False},
        {"notation": "QC", "label": "Volkswirtschaftstheorie", "tiefe": 2, "ober": "Q", "thema": True},
        {"notation": "QC 1", "label": "Allgemeines", "tiefe": 3, "ober": "QC", "thema": False},
        {"notation": "QC 10", "label": "Konjunkturtheorie", "tiefe": 4, "ober": "QC 1", "thema": True,
         "gnd": [["4032134-4", "Konjunktur"]]},
        {"notation": "QC 11", "label": "Wachstumstheorie", "tiefe": 4, "ober": "QC 1", "thema": True},
    ]
    RT.KLASSEN.write_text("".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen), encoding="utf-8")
    RT.lade_klassen.cache_clear()
    qc = R.uri("QC")
    RT.KANDIDATEN_RVK.write_text(json.dumps({"embedding_modell": "test", "themen": {
        qc: {"kandidaten": {fach["notation"]: 0.9}, "handmapping": []}}}), encoding="utf-8")
    wahl = [{"code": fach["notation"], "begruendung": "passt", "belegbegriffe": []}]
    RT.STIMMEN_RVK_FAECHER.write_text("".join(json.dumps({"id": f"t{v}", "stimme": v, "antworten": {qc: wahl}}) + "\n"
                                              for v in range(3)), encoding="utf-8")
    vorhanden = {"id": "https://d-nb.info/gnd/4032134-4", "label_de": "Konjunktur",
                 "studienfach": [voc.ref(fach["uri"])]}
    monkeypatch.setattr(RT, "_vorhandene", lambda: [vorhanden])
    RT.repo_themen.cache_clear()
    yield fach
    RT.lade_klassen.cache_clear()
    RT.repo_themen.cache_clear()


def test_pfade_werden_zusammengesetzt(rvk_dateien):
    k = {x["notation"]: x for x in RT.lade_klassen()}
    assert set(k) == {"QC", "QC 1", "QC 10", "QC 11"}
    assert k["QC 10"]["pfad"] == ["Wirtschaftswissenschaften", "Volkswirtschaftstheorie", "Allgemeines"]
    assert k["QC 10"]["fachsystematik"]["notation"] == "Q" and k["QC 10"]["gnd"][0]["id"] == "4032134-4"


def test_zusammenfuehren_vererbt_fach_und_verknuepft(rvk_dateien):
    fach = rvk_dateien
    prog = RT.build_rvk_zusammenfuehren(stimmen=3)
    assert prog.status.startswith("fertig")
    rows = {r["rvk"]["notation"]: r for r in map(json.loads, RT.THEMEN_RVK.read_text(encoding="utf-8").splitlines())}
    # QC 10 hat dieselbe GND-ID wie ein vorhandenes Thema im selben Fach: Beleg statt neues Thema
    assert "QC 10" not in rows
    belege = json.loads(RT.BELEGE_RVK.read_text(encoding="utf-8"))
    assert belege["https://d-nb.info/gnd/4032134-4"][0]["notation"] == "QC 10"
    # QC 11 erbt das Fach von QC (über die nicht abgestimmte Oberklasse „Allgemeines“ hinweg)
    r = rows["QC 11"]
    assert r["studienfach"][0]["uri"] == fach["uri"] and r["mapping"]["fach_geerbt_von"] == "QC"
    assert r["mapping"]["status"] == "gesichert" and r["art"] == "rvk" and r["quelle"]["lizenz"] == "CC0-1.0"
    # ohne Lehrebenen-Stimmen: Heuristik nach Tiefe, als unsicher gekennzeichnet
    assert r["lehrebene"] == "vertiefung" and r["mapping"]["lehrebene"]["status"] == "unsicher"


def test_hochschule_filter_quelle_und_tiefe():
    b = get("hochschule")
    opt = b.auswahl_optionen()
    assert "rvk" in opt["quellen"]["werte"] and "rvk" in opt["quellen"]["standard"]
    assert opt["rvk_tiefe"]["standard"] == ["2", "3", "4"]
    t = {"id": "x", "art": "rvk", "rvk": {"tiefe": 5}, "mapping": {"status": "gesichert",
                                                                   "lehrebene": {"status": "gesichert"}}}
    assert not b.passt(t, b.auswahl_pruefen({}))  # Tiefe 5 ist standardmäßig aus, nur per Filter
    alle = {"rvk_tiefe": ["2", "3", "4", "5"]}
    assert b.passt(t, b.auswahl_pruefen(alle))
    assert not b.passt(t, b.auswahl_pruefen({"rvk_tiefe": ["2", "3"]}))
    assert not b.passt(t, b.auswahl_pruefen({**alle, "quellen": ["openalex", "gnd"]}))
    assert b.belege_des_themas({"belege": {"rvk": [{"notation": "QC 10"}]}, "studienfach": []}) == {"rvk"}
