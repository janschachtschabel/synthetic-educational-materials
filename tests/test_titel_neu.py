import json

from sem import schema as S
from sem.titel_neu import F_TITEL_ALT, titel_ersetzen, titel_neu
from sem.llm import LLMConfig
from sem.prompts import load_prompts

VOLLTEXT = " ".join(["Eine Linse bricht Licht und erzeugt ein Bild."] * 40)


def _rec(i=0):
    return {"id": f"m{i}", S.F_BEREICH: "elementar", "synth:thema": {"label_de": "Licht"},
            S.F_TITLE: "Linse Licht Brechung Bild Brennweite", S.F_FULLTEXT: VOLLTEXT,
            S.F_DESCRIPTION: "bleibt", S.F_PROVENANCE: {S.F_TITLE: {"quelle": "LLM"}},
            S.F_GENERATION: {"modell": "alt", "ausgabesprache": "de"}}


class Client:
    def __init__(self, antworten):
        self.antworten, self.anfragen = list(antworten), []

    def chat(self, system, user, json_mode=False):
        self.anfragen.append(user)
        return self.antworten.pop(0)


def _meta():
    return {"modell": "fake", "datum": "2026-11-05", "prompt_hash": {"elementar": "abc"}}


def _ersetzen(antworten, monkeypatch):
    from sem.bereiche import get

    b = get("elementar")
    # prompt_values braucht einen vollständigen Seed; für den Test genügen feste Werte
    monkeypatch.setattr(type(b), "prompt_values", lambda self, seed: {"textsorte": "Fingerspiel", "thema": "Licht"})
    client = Client(antworten)
    return titel_ersetzen(client, _rec(), {"elementar": load_prompts("elementar")}, _meta()), client


def test_titel_ersetzen(monkeypatch):
    rec, client = _ersetzen(["Licht: Linse und Bild", "Wie eine Linse ein Bild erzeugt"], monkeypatch)
    assert rec[S.F_TITLE] == "Wie eine Linse ein Bild erzeugt" and rec[F_TITEL_ALT] == "Linse Licht Brechung Bild Brennweite"
    assert rec[S.F_DESCRIPTION] == "bleibt" and rec[S.F_FULLTEXT] == VOLLTEXT
    assert rec[S.F_PROVENANCE][S.F_TITLE]["modell"] == "fake"
    info = rec[S.F_GENERATION]["titel_neu"]
    assert info["neu_angefragt"] == 1 and info["prompt_hash"] == "abc" and "behalten" not in info
    assert rec[S.F_GENERATION]["modell"] == "alt"
    u = client.anfragen[0]  # wie in der Titel-Stufe: Volltext, aber Titel/Beschreibung/Schlagworte leer
    assert "Titel, der den INHALT" in u and VOLLTEXT in u and "Titel: - / Beschreibung: - / Schlagworte: -" in u


def test_titel_behalten_und_weiche_pruefung(monkeypatch):
    rec, _ = _ersetzen(["Licht: a", "Licht – b", "x"], monkeypatch)
    assert rec[S.F_TITLE] == "Linse Licht Brechung Bild Brennweite" and "behalten" in rec[S.F_GENERATION]["titel_neu"]
    # Materialtyp im Titel: zweimal abgelehnt, beim letzten Versuch übernommen
    rec, client = _ersetzen(["Fingerspiel zu Licht und Linsen"] * 3, monkeypatch)
    assert rec[S.F_TITLE] == "Fingerspiel zu Licht und Linsen" and len(client.anfragen) == 3


def test_titel_neu_datei(tmp_path, monkeypatch):
    import sem.llm as llm
    from sem.bereiche import get

    b = get("elementar")
    monkeypatch.setattr(type(b), "prompt_values", lambda self, seed: {"thema": "Licht"})
    ein = tmp_path / "materialien_x.jsonl"
    ein.write_text("".join(json.dumps(_rec(i), ensure_ascii=False) + "\n" for i in range(3)), encoding="utf-8")

    class FakeLLM(Client):
        def __init__(self, cfg):
            super().__init__(["Brechung von Licht an Linsen"] * 10)

    monkeypatch.setattr(llm, "LLMClient", FakeLLM)
    out = tmp_path / "neu.jsonl"
    prog = titel_neu(ein, out, LLMConfig(api_key="x", workers=2), limit=2, on_progress=None)
    assert prog.done == 2
    zeilen = [json.loads(z) for z in out.read_text(encoding="utf-8").splitlines()]
    assert {z["id"] for z in zeilen} == {"m0", "m1"} and all(z[S.F_TITLE] == "Brechung von Licht an Linsen" for z in zeilen)
    assert json.loads(ein.read_text(encoding="utf-8").splitlines()[0])[S.F_TITLE] == "Linse Licht Brechung Bild Brennweite"
    assert titel_neu(ein, out, LLMConfig(api_key="x"), on_progress=None).skipped == 2  # fortsetzbar
