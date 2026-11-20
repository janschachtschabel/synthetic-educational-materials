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


LERNZIELE_JSON = json.dumps({"lernziele": [
    {"lernziel": "Die Studierenden können den Begriff des Eigenwerts definieren", "operator": "definieren",
     "stufe": "erinnern"},
    {"lernziel": "Die Studierenden können Eigenwerte einer 2x2-Matrix berechnen", "operator": "berechnen",
     "stufe": "anwenden"}]})
SUCHANFRAGEN_JSON = json.dumps({"suchanfragen": ["eigenwerte", "eigenwerte berechnen", "spektralsatz",
                                                 "diagonalisierung matrix übung", "symmetrische matrix"]})


class Antworten:
    """Gültige Antworten je Feld; der erste Titel ist zu kurz und wird neu angefragt."""

    def __init__(self, prompts, titel_kaputt=1):
        # metadaten zuerst: der kombinierte Auftrag enthält die Marker aller Felddateien
        self.marker = {"metadaten": _marker(prompts["metadaten"]), **{n: _marker(prompts[n]) for n in FELD_PROMPTS}}
        self.titel_kaputt = titel_kaputt

    def __call__(self, user):
        feld = next((n for n, m in self.marker.items() if m in user), "volltext")
        if feld == "metadaten":
            einzeln = {f: self(self._nur(f)) for f in ("titel", "beschreibung", "keywords")}
            return json.dumps({**einzeln, "keywords": einzeln["keywords"].split(", "),
                               **json.loads(LERNZIELE_JSON), **json.loads(SUCHANFRAGEN_JSON),
                               **json.loads(self(self._nur("fragen_antworten")))}, ensure_ascii=False)
        if feld == "volltext":
            return " ".join(["Wort"] * 1000)
        if feld == "titel":
            self.titel_kaputt -= 1
            return "Kurz" if self.titel_kaputt >= 0 else "Ein ausreichend langer Titel zum Thema"
        if feld == "beschreibung":
            return "Beschreibung des Materials. " * 12
        if feld == "keywords":
            return "Eins, Zwei, Drei, Vier, Fünf"
        if feld == "inhaltstyp":
            return next(z for z in user.splitlines() if z.startswith("- ") and "→" in z).split("→")[1].split(":")[0].strip()
        if feld == "lernziele":
            return LERNZIELE_JSON
        if feld == "suchanfragen":
            return SUCHANFRAGEN_JSON
        return json.dumps({"paare": [{"frage": f"F{i}?", "antwort": "A.", "typ": "verstehen"} for i in range(10)]})

    def _nur(self, feld):
        return self.marker[feld]


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
    lauf = _lauf(path, tmp_path, client, kombiniert=False)
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
        for feld in (S.F_TITLE, S.F_DESCRIPTION, S.F_KEYWORD, S.F_FULLTEXT, S.F_QA, S.F_LRT_CHECK, S.F_PROVENANCE,
                     S.F_LERNZIELE, S.F_SUCHANFRAGEN):
            assert b[feld] == sofort[feld]
        assert b[S.F_GENERATION]["verarbeitung"] == "batch"
        assert b[S.F_GENERATION]["tokens"]["aufrufe"] == 8 + b[S.F_GENERATION]["neu_angefragt"].get("titel", 0)
    # Batch-Anfragen ohne service_tier, JSON-Modus nur für Frage-Antwort-Paare, Lernziele und Suchanfragen
    bodies = [r["body"] for r in client.anfragen]
    assert not any("service_tier" in x for x in bodies)
    assert all(("response_format" in x) == r["custom_id"].split("|")[1].startswith(("fragen", "lernziele", "such"))
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


def test_batch_kombiniert_wie_sofort(seeds_datei, tmp_path):
    """Kombiniert: Volltext, dann Metadaten und Inhaltstyp-Prüfung in einer Runde; nur der unbrauchbare Titel wird
    danach einzeln nachgefragt. Batch und sofort ergeben dieselben Materialien."""
    path, seeds = seeds_datei
    prompts = load_prompts("hochschule")
    client = FakeOpenAI(Antworten(prompts, titel_kaputt=1))
    lauf = _lauf(path, tmp_path, client)
    assert lauf.kombiniert and lauf.stand["parameter"]["metadaten"] == "kombiniert"
    runden = 0
    while not lauf.weiter()["abgeschlossen"]:
        runden += 1
        assert runden < 10
    assert lauf.stand["runde"] == 3  # Volltext, Metadaten + Inhaltstyp, Titel einzeln (ein Material)
    gefragt = [r["custom_id"].split("|")[1] for r in client.anfragen]
    assert sorted(gefragt) == sorted(["volltext"] * 3 + ["metadaten"] * 3 + ["inhaltstyp"] * 3 + ["titel"])
    out = {r["id"]: r for r in _zeilen(tmp_path / "materialien_t.jsonl")}
    assert set(out) == {s["id"] for s in seeds}
    meta = [r["body"] for r in client.anfragen if "|metadaten|" in r["custom_id"]][0]
    assert "response_format" in meta and "## paare" in meta["messages"][1]["content"]

    class Sofort:
        def __init__(self):
            self.a = Antworten(prompts, titel_kaputt=1)

        def chat(self, system, user, json_mode=False):
            return self.a(user)

    for s in seeds:
        sofort = generate_one(Sofort(), s, {"hochschule": prompts}, lauf.meta, kombiniert=True)
        b = out[s["id"]]
        for feld in (S.F_TITLE, S.F_DESCRIPTION, S.F_KEYWORD, S.F_FULLTEXT, S.F_QA, S.F_LRT_CHECK, S.F_PROVENANCE,
                     S.F_LERNZIELE, S.F_SUCHANFRAGEN):
            assert b[feld] == sofort[feld]
        assert b[S.F_GENERATION]["metadaten"] == "kombiniert"
        assert "metadaten" not in b
        assert b[S.F_GENERATION]["tokens"]["aufrufe"] == 3 + b[S.F_GENERATION]["neu_angefragt"].get("titel", 0)


def test_kombiniert_unlesbar_dann_einzeln():
    """Kein JSON: einmal wiederholen, danach jedes Feld einzeln; ein unbrauchbares Feld zählt als Fehlversuch."""
    from sem.generate import Material

    class B:
        key = "hochschule"

        def prompt_values(self, seed):
            return {}

        def umfang(self, seed):
            return (300, 600)

        def sprachprofil(self, seed):
            return ""

        def inhaltstyp_kategorien(self):
            return []

    m = Material({"id": "x", S.F_UMFANG: {"von": 300, "bis": 600}}, {"hochschule": load_prompts("hochschule")},
                 inhaltstyp_pruefen=False, bereich=B(), kombiniert=True)
    m.felder["volltext"] = "Text " * 400
    assert m.stufe == ["metadaten"]
    assert m.antwort("metadaten", "kein json") is False and m.stufe == ["metadaten"]
    assert m.antwort("metadaten", "immer noch keins") is False
    assert m.stufe == ["titel"] and "metadaten" in m.felder
    m2 = Material({"id": "x", S.F_UMFANG: {"von": 300, "bis": 600}}, {"hochschule": load_prompts("hochschule")},
                  inhaltstyp_pruefen=False, bereich=B(), kombiniert=True, zustand={"felder": {"volltext": "T " * 400}})
    assert m2.antwort("metadaten", json.dumps({"titel": "Kurz", "beschreibung": "Satz eins. " * 40,
                                                "keywords": ["A", "B", "C", "D", "E"]})) is True
    assert m2.neu == {"titel": 1, "lernziele": 1, "suchanfragen": 1, "fragen_antworten": 1}
    assert m2.gruende["titel"] == ["Titel mit 4 Zeichen"] and m2.zustand()["gruende"] == m2.gruende
    assert m2.felder["keywords"] == ["A", "B", "C", "D", "E"] and "beschreibung" in m2.felder
    assert m2.stufe == ["titel"]
    assert m2.zustand()["kombiniert"] is True
    # Untertitel: der erste Teil wird übernommen, ohne Neuanfrage
    m3 = Material({"id": "x", S.F_UMFANG: {"von": 300, "bis": 600}}, {"hochschule": load_prompts("hochschule")},
                  inhaltstyp_pruefen=False, bereich=B(), kombiniert=True, zustand={"felder": {"volltext": "T " * 400}})
    m3.antwort("metadaten", json.dumps({"titel": "Stromkreis und Glühlampe: Ein einfacher Versuch"}) + "\n{}")
    assert m3.felder["titel"] == "Stromkreis und Glühlampe" and "titel" not in m3.neu
    assert m3.gruende["titel"][0].startswith("Untertitel abgeschnitten")


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


def test_paket_begrenzt_materialien_je_batch(seeds_datei, tmp_path):
    """Höchstens paket Materialien je Batch; die zwei Anfragen eines Materials (Metadaten, Inhaltstyp) bleiben
    zusammen im selben Batch."""
    path, seeds = seeds_datei
    client = FakeOpenAI(Antworten(load_prompts("hochschule"), titel_kaputt=0))
    lauf = _lauf(path, tmp_path, client, paket=2)
    assert len(lauf.weiter()["batches_laufend"]) == 2  # 3 Materialien, höchstens 2 je Batch
    st = lauf.weiter()  # Runde 2: Metadaten und Inhaltstyp
    teile = [b for b in lauf.stand["batches"] if not b["ausgewertet"]]
    assert [len(b["anfragen"]) for b in teile] == [4, 2]
    for b in teile:
        sids = [c.split("|")[0] for c in b["anfragen"]]
        assert len(set(sids)) <= 2 and all(sids.count(s) == 2 for s in sids)
    while not st["abgeschlossen"]:
        st = lauf.weiter()
    assert len(read_done(tmp_path / "materialien_t.jsonl")) == 3


def test_metadaten_ohne_denken_nur_anthropic(seeds_datei):
    cfg = LLMConfig(anbieter="anthropic", api_key="x", model="m", reasoning_effort="low")
    assert cfg.parameter("s", "u", denken=False)["thinking"] == {"type": "disabled"}
    assert "thinking" not in cfg.parameter("s", "u")
    assert "thinking" not in LLMConfig(anbieter="anthropic", model="m", reasoning_effort="max").parameter(
        "s", "u", denken=False)  # disabled nur bis effort high erlaubt
    assert "thinking" not in LLMConfig(model="m").parameter("s", "u", denken=False)  # OpenAI-kompatibel: unverändert
    from sem.generate import Material

    m = Material(seeds_datei[1][0], {"hochschule": load_prompts("hochschule")}, kombiniert=True,
                 denken_metadaten=False)
    assert [a.denken for a in m.anfragen()] == [True]  # Volltext denkt
    m.felder["volltext"] = "Wort " * 1000
    assert {a.feld: a.denken for a in m.anfragen()} == {"metadaten": False, "inhaltstyp": True}
