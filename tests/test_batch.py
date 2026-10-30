"""Batch-Verarbeitung gegen eine nachgebaute Batch-Schnittstelle: gleiche Materialien wie sofort, fortsetzbar."""

import json
from types import SimpleNamespace

import pytest

from sem import batch as B
from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.generate import generate_one
from sem.llm import LLMConfig, read_done
from sem.prompts import FELD_PROMPTS, load_prompts


def _marker(template):
    return max((z.strip() for z in template.splitlines() if z.strip() and "{{" not in z), key=len)


class Antworten:
    """Gültige Antworten je Feld; der erste Titel ist zu kurz und wird neu angefragt."""

    def __init__(self, prompts, titel_kaputt=1):
        self.marker = {n: _marker(prompts[n]) for n in FELD_PROMPTS}
        self.titel_kaputt = titel_kaputt

    def __call__(self, user):
        feld = next((n for n, m in self.marker.items() if m in user), "volltext")
        if feld == "volltext":
            return " ".join(["Wort"] * 200)
        if feld == "titel":
            self.titel_kaputt -= 1
            return "Kurz" if self.titel_kaputt >= 0 else "Ein ausreichend langer Titel zum Thema"
        if feld == "beschreibung":
            return "Beschreibung des Materials. " * 12
        if feld == "keywords":
            return "Eins, Zwei, Drei, Vier, Fünf"
        if feld == "inhaltstyp":
            return next(z for z in user.splitlines() if z.startswith("- ") and "→" in z).split("→")[1].strip()
        return json.dumps({"paare": [{"frage": f"F{i}?", "antwort": "A.", "typ": "verstehen"} for i in range(10)]})


class FakeOpenAI:
    """files.create/content und batches.create/retrieve; ein Batch ist beim ersten retrieve fertig."""

    def __init__(self, antworten, fehler_ids=(), ablaufen=0, abgelehnt=0, ablehnung="token_limit_exceeded"):
        self.antworten, self.dateien, self.batches = antworten, {}, {}
        self.abgelehnt, self.ablehnung = abgelehnt, ablehnung  # so viele Batches lehnt die API als Ganzes ab
        self.fehler_ids = set(fehler_ids)  # custom_id-Präfixe, die einmal mit Fehler zurückkommen
        self.ablaufen = ablaufen          # so viele Batches laufen ab, ohne Ergebnis
        self.files = SimpleNamespace(create=self._datei_anlegen, content=self._datei_inhalt)
        self.batches = SimpleNamespace(create=self._batch_anlegen, retrieve=self._batch_abrufen)
        self._batches, self.anfragen = {}, []

    def _datei_anlegen(self, file, purpose, timeout=None):
        fid = f"file-{len(self.dateien)}"
        self.dateien[fid] = file.read().decode("utf-8")
        return SimpleNamespace(id=fid)

    def _datei_inhalt(self, fid, timeout=None):
        return SimpleNamespace(text=self.dateien[fid])

    def _batch_anlegen(self, input_file_id, endpoint, completion_window, metadata):
        bid = f"batch-{len(self._batches)}"
        self._batches[bid] = input_file_id
        return SimpleNamespace(id=bid, status="validating")

    def _batch_abrufen(self, bid):
        if self.abgelehnt:
            self.abgelehnt -= 1
            return SimpleNamespace(status="failed", output_file_id=None, error_file_id=None,
                                   errors={"data": [{"code": self.ablehnung, "message": "abgelehnt"}]})
        if self.ablaufen:
            self.ablaufen -= 1
            return SimpleNamespace(status="expired", output_file_id=None, error_file_id=None)
        ok, err = [], []
        for zeile in self.dateien[self._batches[bid]].splitlines():
            r = json.loads(zeile)
            self.anfragen.append(r)
            cid = r["custom_id"]
            treffer = next((p for p in self.fehler_ids if cid.startswith(p)), None)
            if treffer:
                self.fehler_ids.discard(treffer)
                err.append({"custom_id": cid, "response": {"status_code": 500, "body": {"error": "Serverfehler"}}})
                continue
            user = r["body"]["messages"][1]["content"]
            ok.append({"custom_id": cid, "response": {"status_code": 200, "body": {
                "choices": [{"message": {"content": self.antworten(user)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                          "completion_tokens_details": {"reasoning_tokens": 5}}}}})
        out = self._datei_anlegen(SimpleNamespace(read=lambda: "\n".join(map(json.dumps, ok)).encode()), "x").id
        errf = self._datei_anlegen(SimpleNamespace(read=lambda: "\n".join(map(json.dumps, err)).encode()), "x").id
        return SimpleNamespace(status="completed", output_file_id=out, error_file_id=errf if err else None)


@pytest.fixture
def seeds_datei(tmp_path):
    b = get("hochschule")
    try:
        b.topics()
    except FileNotFoundError:
        pytest.skip("keine Themen gebaut")
    seeds = build_seeds(b, 3, seed=5)
    p = tmp_path / "seeds_t.jsonl"
    p.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds), encoding="utf-8")
    return p, seeds


def _lauf(seeds_path, tmp_path, client, **kw):
    return B.BatchLauf(seeds_path, tmp_path / "materialien_t.jsonl", LLMConfig(api_key="x", model="m"),
                       client=client, **kw)


def _zeilen(p):
    return [json.loads(z) for z in p.read_text(encoding="utf-8").splitlines() if z.strip()]


def test_batch_ergibt_dieselben_materialien_wie_sofort(seeds_datei, tmp_path):
    path, seeds = seeds_datei
    prompts = load_prompts("hochschule")
    client = FakeOpenAI(Antworten(prompts, titel_kaputt=1))
    lauf = _lauf(path, tmp_path, client)
    runden = 0
    while not lauf.weiter()["abgeschlossen"]:
        runden += 1
        assert runden < 10
    # 5 Stufen; der zu kurze Titel braucht eine Runde mehr (nur für dieses Material)
    assert lauf.stand["runde"] == 6
    out = {r["id"]: r for r in _zeilen(tmp_path / "materialien_t.jsonl")}
    assert set(out) == {s["id"] for s in seeds}

    class Sofort:
        def __init__(self):
            self.a = Antworten(prompts, titel_kaputt=1)

        def chat(self, system, user, json_mode=False):
            return self.a(user)

    for s in seeds:
        sofort = generate_one(Sofort(), s, {"hochschule": prompts}, lauf.meta)
        b = out[s["id"]]
        for feld in (S.F_TITLE, S.F_DESCRIPTION, S.F_KEYWORD, S.F_FULLTEXT, S.F_QA, S.F_LRT_CHECK, S.F_PROVENANCE):
            assert b[feld] == sofort[feld]
        assert b[S.F_GENERATION]["verarbeitung"] == "batch"
        assert b[S.F_GENERATION]["tokens"]["aufrufe"] == 6 + b[S.F_GENERATION]["neu_angefragt"].get("titel", 0)
    # Batch-Anfragen ohne service_tier, JSON-Modus nur für die Frage-Antwort-Paare
    bodies = [r["body"] for r in client.anfragen]
    assert not any("service_tier" in x for x in bodies)
    assert all(("response_format" in x) == r["custom_id"].split("|")[1].startswith("fragen")
               for x, r in zip(bodies, client.anfragen))


def test_fehler_und_abgelaufene_batches_werden_neu_gestellt(seeds_datei, tmp_path):
    path, seeds = seeds_datei
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=0), fehler_ids={seeds[0]["id"]},
                        ablaufen=1)
    lauf = _lauf(path, tmp_path, client)
    for _ in range(12):
        if lauf.weiter()["abgeschlossen"]:
            break
    assert len(read_done(tmp_path / "materialien_t.jsonl")) == 3
    assert not (tmp_path / "materialien_t.fehler.jsonl").exists()


def test_unbrauchbares_feld_nach_drei_versuchen_fehlgeschlagen(seeds_datei, tmp_path):
    path, seeds = seeds_datei
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=99))
    lauf = _lauf(path, tmp_path, client)
    for _ in range(12):
        st = lauf.weiter()
        if st["abgeschlossen"]:
            break
    assert st["fehlgeschlagen"] == 3 and st["fertig"] == 0
    assert all("titel nach 3 Versuchen" in f["fehler"] for f in _zeilen(tmp_path / "materialien_t.fehler.jsonl"))


def test_fortsetzen_mit_neuem_objekt_und_aufteilung(seeds_datei, tmp_path, monkeypatch):
    path, seeds = seeds_datei
    monkeypatch.setattr(B, "MAX_ANFRAGEN", 2)
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=0))
    st = _lauf(path, tmp_path, client).weiter()
    assert len(st["batches_laufend"]) == 2  # 3 Volltext-Anfragen, höchstens 2 je Batch
    for _ in range(10):  # jedes Mal ein neues Objekt: der Stand kommt aus stand.json
        if _lauf(path, tmp_path, client).weiter()["abgeschlossen"]:
            break
    assert len(read_done(tmp_path / "materialien_t.jsonl")) == 3
    with pytest.raises(ValueError, match="anderen Einstellungen"):
        _lauf(path, tmp_path, client, ausgabesprache="en")


def test_warteschlangen_limit_zaehlt_nicht_als_fehlversuch(seeds_datei, tmp_path):
    path, seeds = seeds_datei
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=0), abgelehnt=2)
    lauf = _lauf(path, tmp_path, client)
    for _ in range(40):
        if lauf.weiter()["abgeschlossen"]:
            break
    # zwei abgelehnte Batches, trotzdem alle fertig; danach weniger gleichzeitig unterwegs
    assert len(read_done(tmp_path / "materialien_t.jsonl")) == 3
    assert not (tmp_path / "materialien_t.fehler.jsonl").exists()
    assert lauf.stand["token_budget"]
    # ausgewertete Batches tragen keine Anfrageliste mehr, nur die Anzahl
    assert all(b["anfragen"] == [] and b["anzahl"] for b in lauf.stand["batches"])


def test_wiederholt_abgelehnte_batches_halten_den_lauf_an(seeds_datei, tmp_path):
    path, _ = seeds_datei
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=0), abgelehnt=99,
                        ablehnung="invalid_request")
    lauf = _lauf(path, tmp_path, client)
    with pytest.raises(RuntimeError, match="nacheinander abgelehnt"):
        for _ in range(10):
            lauf.weiter()
    assert not (tmp_path / "materialien_t.fehler.jsonl").exists()
