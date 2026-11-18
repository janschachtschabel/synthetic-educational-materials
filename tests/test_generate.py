import json

import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.generate import (generate_one, parse_inhaltstyp, parse_keywords, parse_lernziele, parse_suchanfragen,
                          parse_titel, titel_nennt_form, titel_reparieren)
from sem.prompts import load_prompts
from tests.test_batch import LERNZIELE_JSON, SUCHANFRAGEN_JSON

VOLLTEXT = " ".join(["Die Eigenwerte einer symmetrischen Matrix sind reell."] * 160)


class FakeClient:
    """Antwortet je nach Auftrag; der erste Titel ist absichtlich zu kurz (wird nachgefragt)."""

    def __init__(self, kategorie):
        self.kategorie = kategorie
        self.calls = []
        self.systeme = []

    def chat(self, system, user, json_mode=False):
        self.calls.append(user)
        self.systeme.append(system)
        if "Auftrag: Schreibe dieses Material" in user:
            return VOLLTEXT
        if "Titel, der den INHALT" in user:
            return "Kurz" if sum("Titel, der den INHALT" in c for c in self.calls) == 1 else \
                "„Eigenwerte Diagonalisierung symmetrischer Matrizen“"
        if "zusammenfassende Beschreibung" in user:
            return "Eigenwerte symmetrischer Matrizen sind reell. " * 10
        if "Schlagworte zum Thema" in user:
            return "Eigenwert, Diagonalisierung, Symmetrische Matrix, Spektralsatz, Orthogonalbasis, eigenwert"
        if "Materialart ausschließlich" in user:
            assert "Materialart: -" in user  # Seed-Wert wird nicht verraten
            return self.kategorie
        if "kompetenzorientierte Lernziele" in user:
            return LERNZIELE_JSON
        if "Suchanfragen, die Lehrende" in user:
            return SUCHANFRAGEN_JSON
        if "Frage-Antwort-Paare" in user:
            return json.dumps({"paare": [{"frage": f"F{i}?", "antwort": "A.", "typ": "verstehen"} for i in range(10)]})
        raise AssertionError(user[:200])

    def chat_json(self, system, user):
        return json.loads(self.chat(system, user, json_mode=True))


@pytest.fixture
def hochschule():
    b = get("hochschule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    return b


def test_titel_reparieren_beim_letzten_versuch():
    """Statt ein bezahltes Material am Titel scheitern zu lassen: Untertitel abschneiden, etwas längere Titel annehmen."""
    assert titel_reparieren("Wechselstrom im Haushalt: Effektivwert und Phasenlage") == "Wechselstrom im Haushalt"
    assert titel_reparieren("Phasen – Effektivwert und Phasenlage im Wechselstromkreis") == \
        "Effektivwert und Phasenlage im Wechselstromkreis"
    lang = "Effektivwert und Phasenlage im Wechselstromkreis eines einfachen Haushalts mit vielen Geräten"
    assert 80 < len(lang) <= 95 and titel_reparieren(lang) == lang
    for kaputt in ("x" * 120, "Natur | Garten", "Kurz: Zu"):
        with pytest.raises(ValueError):
            titel_reparieren(kaputt)


def test_parser():
    assert parse_titel("„Lichtreaktion der Fotosynthese“.\nmehr") == "Lichtreaktion der Fotosynthese"
    assert parse_titel("α-Helix und β-Faltblatt in Proteinen") == "α-Helix und β-Faltblatt in Proteinen"
    assert parse_titel("Wie eine Linse ein Bild erzeugt?") == "Wie eine Linse ein Bild erzeugt?"
    for fach in ("Untreue nach § 266 StGB", "Wasser bei 100 °C sieden lassen", "Bose–Einstein-Kondensate im Labor",
                 "Goethes „Faust“ im Unterricht", "Masse und Energie nach E = mc²"):
        assert parse_titel(fach) == fach
    assert parse_titel("ABBA und der Erfolg von „Waterloo“") == "ABBA und der Erfolg von „Waterloo“"
    for kaputt in ("Wechselstrom: Effektivwert und Phasenlage", "Wechselstrom – Effektivwert und Phasenlage",
                   "Bodenschutz und nachhaltige Nutzung решений", "Gebäudeausrüstung im Bestand♀♀",
                   "Automaten und formale Sprachen bg", "x" * 81, "Biologische Vielfalt in Lebensräumen}",
                   "Vielfalt [Arten] im Garten", "Die #Natur im Garten", "Natur_im Garten", "Natur | Garten",
                   "Heißluftballon bauen und Funktionsweise kennenlernen", "Biologische Vielfalt entdecken"):
        with pytest.raises(ValueError):
            parse_titel(kaputt)
    assert parse_keywords("- Bruchrechnung\n- Dezimalzahl, Prozentrechnung; bruchrechnung, Kürzen, Hauptnenner") == \
        ["Bruchrechnung", "Dezimalzahl", "Prozentrechnung", "Kürzen", "Hauptnenner"]
    with pytest.raises(ValueError):
        parse_keywords("Bruchrechnung, Dezimalzahl, Prozentrechnung, Kürzen")  # Prompt verlangt mindestens 5
    kat = [{"id": "fallstudie", "name": "Fallstudie", "uri": "u"}]
    assert parse_inhaltstyp(" `fallstudie` ", kat)["uri"] == "u"
    with pytest.raises(ValueError):
        parse_inhaltstyp("quiz", kat)


def test_inhaltstyp_aus_einem_satz():
    """Antwortet das Modell mit einem Satz, gilt die Kategorie, wenn genau eine darin vorkommt."""
    kat = [{"id": "anleitung", "name": "Anleitung", "uri": "a"}, {"id": "glossar", "name": "Glossar", "uri": "g"},
           {"id": "uebungsblatt", "name": "Übungsblatt", "uri": "u"}]
    assert parse_inhaltstyp("Das Material ist eine Anleitung: Schritt für Schritt.", kat)["uri"] == "a"
    assert parse_inhaltstyp("Kategorie: Glossar", kat)["uri"] == "g"
    for unklar in ("kein Übungsblatt, sondern eine Anleitung", "Bedienungsanleitung für das Gerät", "ein Quiz"):
        with pytest.raises(ValueError):
            parse_inhaltstyp(unklar, kat)


def test_letzter_versuch_ein_lernziel_genuegt():
    eins = {"lernziele": [{"lernziel": "Die Studierenden können Eigenwerte berechnen", "stufe": "anwenden"}]}
    with pytest.raises(ValueError):
        parse_lernziele(eins)
    assert len(parse_lernziele(eins, minimum=1)) == 1


def test_generate_one_einzelaufrufe_und_inhaltstyp_pruefung(hochschule):
    seed = build_seeds(hochschule, 1, seed=3)[0]
    kat = {k["uri"]: k for k in hochschule.inhaltstyp_kategorien()}
    client = FakeClient(kat[seed[S.F_LRT][0]]["id"])
    rec = generate_one(client, seed, {"hochschule": load_prompts("hochschule")}, {"modell": "fake"})
    assert rec[S.F_TITLE] == "Eigenwerte Diagonalisierung symmetrischer Matrizen"
    assert rec[S.F_KEYWORD] == ["Eigenwert", "Diagonalisierung", "Symmetrische Matrix", "Spektralsatz", "Orthogonalbasis"]
    assert len(rec[S.F_QA]) == 10 and rec[S.F_LRT_CHECK]["uebereinstimmung"]
    # Volltext, Titel (2x), Beschreibung, Keywords, Inhaltstyp, Fragen
    assert len(client.calls) == 9  # + Lernziele, Suchanfragen
    assert len(rec[S.F_LERNZIELE]) == 2 and len(rec[S.F_SUCHANFRAGEN]) == 5
    # spätere Felder sehen die früheren
    assert "Titel: Eigenwerte Diagonalisierung" in client.calls[-1]


def test_ausgabesprache(hochschule):
    seed = build_seeds(hochschule, 1, seed=3)[0]
    kat = {k["uri"]: k for k in hochschule.inhaltstyp_kategorien()}
    client = FakeClient(kat[seed[S.F_LRT][0]]["id"])
    prompts = {"hochschule": load_prompts("hochschule")}
    rec = generate_one(client, seed, prompts, {"modell": "fake"})
    assert rec[S.F_LANGUAGE] == ["de"] and not any("Ausgabesprache" in x for x in client.systeme)
    client = FakeClient(kat[seed[S.F_LRT][0]]["id"])
    rec = generate_one(client, seed, prompts, {"modell": "fake"}, ausgabesprache="en")
    assert all("auf Englisch" in x for x in client.systeme)
    assert "Sprache: Englisch" in client.calls[-1]
    assert rec[S.F_LANGUAGE] == ["en"] and S.F_LANGUAGE in rec[S.F_PROVENANCE]


def test_auswahl_nach_status_quelle_und_belegen(hochschule):
    alle = hochschule.topics()
    std = hochschule.themen_auswahl()
    assert all(t["mapping"]["status"] != "unsicher" for t in std)
    nur_gnd = hochschule.themen_auswahl({"quellen": ["gnd"], "belege": ["lehrbuch"]})
    assert nur_gnd and all(t["art"] == "gnd" and t["belege"]["katalog"]["lehrbuecher"] for t in nur_gnd)
    assert len(nur_gnd) < len(std) <= len(alle)
    with pytest.raises(ValueError):
        hochschule.auswahl_pruefen({"fach_status": ["sicher"]})
    seeds = build_seeds(hochschule, 50, seed=1, auswahl={"belege": ["wlo"]})
    for s in seeds:
        p = s[S.F_PROVENANCE][S.F_THEMA]
        assert "wlo" in p["belege"]["erfuellt"] and p["belege"]["wlo"]["quelle"]["uri"] == "https://wirlernenonline.de"
        assert s["synth:seed_parameter"]["auswahl"]["belege"] == ["wlo"]


def test_titel_nennt_form():
    kita = {"textsorte": "neues Lied, Fingerspiel oder Reim zum Thema", "inhaltstyp": "Musik",
            "zielgruppe": "pädagogische Fachkräfte in der Kita", "thema": "Motorrad"}
    assert titel_nennt_form("Bewegungsreim zum Motorrad", kita) == "bewegungsreim"
    assert titel_nennt_form("Freizeit und Hobbys im Fingerspiel", kita) == "fingerspiel"
    assert titel_nennt_form("Bauen mit LEGO-Steinen in der Kita", kita) == "kita"
    assert titel_nennt_form("Motorrad und Verkehrsregeln", kita) is None
    # gehört das Wort zum Thema, ist es kein Hinweis auf den Materialtyp
    assert titel_nennt_form("Urheberrecht in der Kita", {**kita, "thema": "Urheberrecht in der Kita"}) is None
    # nur Wörter, die auch in den Metadaten stehen
    assert titel_nennt_form("Quiz und Rätsel im Mittelalter", {"inhaltstyp": "Arbeitsblatt"}) is None


def test_parse_lernziele():
    z = parse_lernziele(json.loads(LERNZIELE_JSON))
    assert [x["stufe"] for x in z] == ["erinnern", "anwenden"] and z[0]["lernziel"].endswith(".")
    vage = {"lernziele": [{"lernziel": "Die Studierenden können Eigenwerte verstehen", "stufe": "verstehen"},
                          {"lernziel": "Die Studierenden können Eigenwerte berechnen", "stufe": "apply"}]}
    with pytest.raises(ValueError):  # „verstehen“ fällt weg, eines reicht nicht
        parse_lernziele(vage)
    vage["lernziele"].append({"lernziel": "Die Studierenden können den Spektralsatz anwenden", "stufe": "x"})
    with pytest.raises(ValueError):  # unbekannte Stufe zählt nicht
        parse_lernziele(vage)
    vage["lernziele"].append({"lernziel": "Die Studierenden können zwei Verfahren vergleichen", "stufe": "analysieren"})
    assert [x["stufe"] for x in parse_lernziele(vage)] == ["anwenden", "analysieren"]


def test_parse_suchanfragen():
    q = parse_suchanfragen({"suchanfragen": ["Eigenwerte", "eigenwerte", "spektralsatz", "matrix übung",
                                             "wie berechnet man eigenwerte einer symmetrischen matrix schnell",
                                             "diagonalisierung."]})
    assert q == ["Eigenwerte", "spektralsatz", "matrix übung", "diagonalisierung"]
    with pytest.raises(ValueError):
        parse_suchanfragen({"suchanfragen": ["eins", "zwei"]})


def test_lernziel_zu_lang_faellt_weg():
    lang = "Die Studierenden können " + "sehr " * 40 + "berechnen"
    daten = json.loads(LERNZIELE_JSON)
    daten["lernziele"].append({"lernziel": lang, "stufe": "anwenden"})
    assert len(parse_lernziele(daten)) == 2


def test_klammerzusaetze_werden_keine_fachaussagen():
    """Stichprobe HS: „(auch E-Book)“ landete in Beschreibungen, RVK-Zusätze wie „(ohne Spaltung und Streuung)“
    wurden als Fachaussage geschrieben."""
    from sem.bereiche.hochschule import thema_und_abgrenzung
    from sem.generate import materialart

    assert materialart("Lehrbuch und Grundlagenwerk (auch E-Book)") == "Lehrbuch und Grundlagenwerk"
    assert materialart("Experiment (Lehr- und Lernmaterial)") == "Experiment"
    assert materialart("Lösungs(beispiel) und Erwartungshorizont") == "Lösungs(beispiel) und Erwartungshorizont"
    assert thema_und_abgrenzung("Kernphysik (ohne Spaltung und Streuung)") == ("Kernphysik", "ohne Spaltung und Streuung")
    assert thema_und_abgrenzung("Magnetresonanztomographie (MRT)") == ("Magnetresonanztomographie (MRT)", "")
