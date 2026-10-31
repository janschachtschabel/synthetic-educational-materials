"""Oberfläche (sem/web): Seiten, Schutz vor fremden Anfragen, Start und Fortsetzen von Läufen, Status, Vorschau."""

import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sem.web import app as web
from sem.web import dateien

HX = {"HX-Request": "true"}


@pytest.fixture
def ordner(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(web.Ordner, "output", out)
    monkeypatch.setattr(web.Ordner, "prozesse", out / ".prozesse")
    gestartet = []

    def starten(name, argv, cfg=None):
        gestartet.append((name, argv, cfg))
    monkeypatch.setattr(web, "starten", starten)
    return out, gestartet


@pytest.fixture
def client(ordner):
    return TestClient(web.app)


def _jsonl(p, n, material=True):
    with p.open("w", encoding="utf-8") as f:
        for i in range(n):
            rec = {"id": f"x-{i:03d}", "synth:thema": {"label_de": f"Thema {i}"}}
            if material:
                rec |= {"synth:volltext": f"# Text {i}\n\nÜber Photosynthese" if i == 7 else f"Text {i}",
                        "cclom:title": f"Titel {i}"}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _lauf(out, name="test", ziel=10, fertig=4, verarbeitung="sofort"):
    seeds = out / f"seeds_{name}.jsonl"
    _jsonl(seeds, ziel, material=False)
    aus = out / f"materialien_{name}.jsonl"
    _jsonl(aus, fertig)
    (out / f"materialien_{name}.lauf.json").write_text(json.dumps({
        "seeds": str(seeds), "limit": None, "ziel": ziel, "ausgabesprache": "de", "inhaltstyp_pruefen": True,
        "verarbeitung": verarbeitung, "modell": "m1", "reasoning_effort": "low",
        "gestartet": "2026-10-31T01:00:00", "zuletzt": "2026-10-31T02:00:00"}), encoding="utf-8")
    return aus


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_alle_seiten_laden(client, ordner, sprache):
    _lauf(ordner[0])
    client.cookies.set("sprache", sprache)
    for u in ["/seeds", "/materialien", "/materialien?aufgabe=fortsetzen", "/vorschau", "/vorschau?art=seeds",
              "/prompts", "/mappings", "/methodik", "/einstellungen", "/laeufe/kopf", "/status/mat"]:
        r = client.get(u)
        assert r.status_code == 200, u
    assert f'<html lang="{sprache}">' in client.get("/seeds").text
    if sprache == "en":
        assert "Start a new run" in client.get("/materialien").text


def test_aenderungen_nur_von_dieser_seite(client):
    assert client.post("/laeufe/mat/anhalten").status_code == 403  # ohne HTMX-Kopf (fremdes Formular)
    assert client.post("/laeufe/mat/anhalten", headers={**HX, "Origin": "https://boese.example"}).status_code == 403
    assert client.get("/seeds", headers={"Host": "boese.example"}).status_code == 403  # DNS-Rebinding
    assert client.post("/laeufe/mat/anhalten", headers={**HX, "Origin": "http://127.0.0.1:8501"}).status_code == 200


def test_neuer_lauf_startet_prozess_mit_gewaehlten_einstellungen(client, ordner):
    out, gestartet = ordner
    _jsonl(out / "seeds_a.jsonl", 5, material=False)
    r = client.post("/materialien/start", headers=HX, data={
        "src": "seeds_a.jsonl", "name": "probe", "limit": "3", "sprache": "en", "verarbeitung": "flex", "workers": "77"})
    assert r.status_code == 200
    name, argv, cfg = gestartet[0]
    assert name == "mat"
    assert argv[:2] == ["materialien", "--seeds"] and argv[argv.index("--out") + 1].endswith("materialien_probe.jsonl")
    assert argv[argv.index("--limit") + 1] == "3" and "--ohne-inhaltstyp-pruefung" in argv  # Häkchen nicht gesetzt
    assert cfg.workers == 77 and cfg.service_tier == "flex"


def test_fortsetzen_zeigt_einstellungen_und_startet_erst_nach_bestaetigung(client, ordner):
    out, gestartet = ordner
    _lauf(out)
    r = client.get("/materialien/fortsetzen/materialien_test.jsonl")
    assert "Einstellungen prüfen" in r.text and "m1" in r.text and "seeds_test.jsonl" in r.text
    assert not gestartet  # Ansehen startet nichts
    client.post("/materialien/fortsetzen/materialien_test.jsonl", headers=HX,
                data={"aktion": "fortsetzen", "verarbeitung": "sofort", "workers": "12"})
    name, argv, cfg = gestartet[0]
    assert argv == ["materialien", "--fortsetzen", "materialien_test.jsonl", "--ohne-flex"]
    assert cfg.workers == 12 and cfg.model == "m1" and cfg.service_tier is None
    client.post("/materialien/fortsetzen/materialien_test.jsonl", headers=HX,
                data={"aktion": "neu", "verarbeitung": "flex", "workers": "5"})
    argv = gestartet[1][1]
    assert argv[argv.index("--out") + 1].endswith("materialien_test_2.jsonl") and gestartet[1][2].service_tier == "flex"


def _prozess(out, name, stand=None, pid=None):
    d = out / ".prozesse"
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"pid": pid or os.getpid(), "befehl": [], "gestartet": "x"}))
    if stand is not None:
        (d / f"{name}.stand.json").write_text(json.dumps(stand))


def test_status_zeigt_phase_kennzahlen_und_haengende_laeufe(client, ordner):
    out, _ = ordner
    jetzt = datetime.now()
    stand = {"total": 200, "done": 50, "failed": 2, "skipped": 10, "retries": 3, "aktiv": 20, "anfragen": 300,
             "status": "läuft", "pro_minute": 24.5, "rest_sekunden": 600, "letzte_meldung": "APIConnectionError",
             "aktualisiert": jetzt.isoformat(timespec="seconds"), "ende": False}
    _prozess(out, "mat", stand)
    t = client.get("/status/mat").text
    assert "läuft" in t and "every 1s" in t and "26.0 %" in t and "24.5 pro Minute" in t
    assert "Letzte Wiederholung wegen" in t and "Anhalten" in t
    assert "Materialien: läuft" in client.get("/laeufe/kopf").text

    daten = client.get("/status/mat/daten")
    assert "every 1s" in daten.text and "Anhalten" not in daten.text and "HX-Trigger" not in daten.headers

    stand["aktualisiert"] = (jetzt - timedelta(minutes=5)).isoformat(timespec="seconds")
    _prozess(out, "mat", stand)
    assert "Keine Rückmeldung" in client.get("/status/mat").text

    _prozess(out, "mat", {**stand, "status": "fertig", "ende": True}, pid=999_999_999)  # Prozess beendet
    t = client.get("/status/mat").text
    assert "fertig" in t and "every 1s" not in t and "Ausblenden" in t
    assert client.get("/status/mat/daten").headers["HX-Trigger"] == "lauf-ende-mat"  # Knöpfe neu zeichnen


def test_vorschau_blaettert_und_sucht_in_der_datei(client, ordner):
    out, _ = ordner
    _jsonl(out / "materialien_v.jsonl", 20)
    t = client.get("/vorschau?datei=materialien_v.jsonl&nr=5").text
    assert "Titel 4" in t and "20 Datensätze" in t
    t = client.get("/vorschau?datei=materialien_v.jsonl&q=PHOTOSYNTHESE").text
    assert "1 Treffer" in t and "Titel 7" in t
    assert "<html" not in client.get("/vorschau?datei=materialien_v.jsonl", headers=HX).text  # nur der Teil


def test_zeilenindex_waechst_mit_und_erkennt_ueberschreiben(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"id": "a"}\n\n{"id": "b"}\n{"id": "hal', encoding="utf-8")
    assert dateien.zeilen(p) == 2  # halbe letzte Zeile zählt nicht
    with p.open("a", encoding="utf-8") as f:
        f.write('b"}\n{"id": "c"}\n')
    assert dateien.zeilen(p) == 4 and dateien.datensatz(p, 3) == {"id": "c"}
    p.write_text('{"id": "neu"}\n', encoding="utf-8")
    assert dateien.zeilen(p) == 1 and dateien.datensatz(p, 0) == {"id": "neu"}


def test_seeds_zusammenfassung_und_start(client, ordner):
    _, gestartet = ordner
    from sem.bereiche import BEREICHE

    standard = BEREICHE["hochschule"]().standard_auswahl()
    form = {"an-hochschule": "1", "n-hochschule": "100", "bal-hochschule": "thema", "zufall": "7",
            **{f"f-hochschule-{k}": v for k, v in standard.items()}}
    t = client.post("/seeds/pruefen", headers=HX, data=form).text
    assert "seeds_hochschule100_s7.jsonl" in t
    client.post("/seeds/erzeugen", headers=HX, data=form)
    name, argv, _ = gestartet[0]
    assert name == "seeds" and argv[argv.index("--anzahl") + 1] == "hochschule=100" and "--seed" in argv
    t = client.post("/seeds/erzeugen", headers=HX, data={"zufall": "7"}).text
    assert "Mindestens einen Bildungsbereich" in t and len(gestartet) == 1


def test_einstellungen_speichern_ohne_key_in_datei(client, ordner):
    out, _ = ordner
    r = client.post("/einstellungen", headers=HX, data={"model": "m2", "api_key": "sk-geheim", "workers": "33",
                                                       "reasoning_effort": "medium", "retries": "4",
                                                       "ausfall_minuten": "5"})
    assert r.status_code == 204 and r.headers["HX-Redirect"].startswith("/einstellungen")
    gespeichert = (out / ".oberflaeche.json").read_text(encoding="utf-8")
    assert "sk-geheim" not in gespeichert and '"workers": 33' in gespeichert
    cfg = web.llm_cfg()
    assert cfg.api_key == "sk-geheim" and cfg.model == "m2" and cfg.reasoning_effort == "medium"
    web._api_key.clear()
