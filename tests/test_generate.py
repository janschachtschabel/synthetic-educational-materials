import json

import pytest

from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.generate import generate_one, parse_inhaltstyp, parse_keywords, parse_titel
from sem.prompts import load_prompts

VOLLTEXT = " ".join(["Die Eigenwerte einer symmetrischen Matrix sind reell."] * 40)


class FakeClient:
    """Antwortet je nach Auftrag; der erste Titel ist absichtlich zu kurz (wird nachgefragt)."""

    def __init__(self, kategorie):
        self.kategorie = kategorie
        self.calls = []

    def chat(self, system, user, json_mode=False):
        self.calls.append(user)
        if "Auftrag: Schreibe dieses Material" in user:
            return VOLLTEXT
        if "Titel, der den INHALT" in user:
            return "Kurz" if sum("Titel, der den INHALT" in c for c in self.calls) == 1 else \
                "„Eigenwerte Diagonalisierung symmetrischer Matrizen“"
        if "zusammenfassende Beschreibung" in user:
            return "Eigenwerte symmetrischer Matrizen sind reell. " * 10
        if "Schlagworte zum Thema" in user:
            return "Eigenwert, Diagonalisierung, Symmetrische Matrix, Spektralsatz, eigenwert"
        if "Materialart ausschließlich" in user:
            assert "Materialart: -" in user  # Seed-Wert wird nicht verraten
            return self.kategorie
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


def test_parser():
    assert parse_titel("„Fotosynthese Lichtreaktion Dunkelreaktion“.\nmehr") == "Fotosynthese Lichtreaktion Dunkelreaktion"
    assert parse_keywords("- Bruchrechnung\n- Dezimalzahl, Prozentrechnung; bruchrechnung, Kürzen") == \
        ["Bruchrechnung", "Dezimalzahl", "Prozentrechnung", "Kürzen"]
    with pytest.raises(ValueError):
        parse_keywords("Mathematik, Zahl")
    kat = [{"id": "fallstudie", "name": "Fallstudie", "uri": "u"}]
    assert parse_inhaltstyp(" `fallstudie` ", kat)["uri"] == "u"
    with pytest.raises(ValueError):
        parse_inhaltstyp("quiz", kat)


def test_generate_one_einzelaufrufe_und_inhaltstyp_pruefung(hochschule):
    seed = build_seeds(hochschule, 1, seed=3)[0]
    kat = {k["uri"]: k for k in hochschule.inhaltstyp_kategorien()}
    client = FakeClient(kat[seed[S.F_LRT][0]]["id"])
    rec = generate_one(client, seed, {"hochschule": load_prompts("hochschule")}, {"modell": "fake"})
    assert rec[S.F_TITLE] == "Eigenwerte Diagonalisierung symmetrischer Matrizen"
    assert rec[S.F_KEYWORD] == ["Eigenwert", "Diagonalisierung", "Symmetrische Matrix", "Spektralsatz"]
    assert len(rec[S.F_QA]) == 10 and rec[S.F_LRT_CHECK]["uebereinstimmung"]
    # Volltext, Titel (2x), Beschreibung, Keywords, Inhaltstyp, Fragen
    assert len(client.calls) == 7
    # spätere Felder sehen die früheren
    assert "Titel: Eigenwerte Diagonalisierung" in client.calls[-1]


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
