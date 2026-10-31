"""Unfertige Läufe erkennen, fortsetzen und unter neuem Namen neu starten."""

import json

from sem.laeufe import freier_name, lauf_pfad, lauf_speichern, lauf_stand, sperre_aufheben, unfertige_laeufe
from sem.llm import LLMConfig


def _seeds(tmp_path, n):
    p = tmp_path / "seeds_t.jsonl"
    p.write_text("".join(json.dumps({"id": f"s{i}"}) + "\n" for i in range(n)), encoding="utf-8")
    return p


def test_abgebrochener_lauf_ist_unfertig_bis_zum_ziel(tmp_path):
    seeds, out = _seeds(tmp_path, 5), tmp_path / "materialien_t.jsonl"
    lauf_speichern(out, seeds, LLMConfig(model="m"), limit=4, inhaltstyp_pruefen=True, ausgabesprache="de",
                   verarbeitung="flex")
    # Abbruch nach zwei Materialien, eins fehlgeschlagen, Sperrdatei liegt noch
    out.write_text('{"id": "s0"}\n{"id": "s1"}\n', encoding="utf-8")
    out.with_suffix(".fehler.jsonl").write_text('{"id": "s2", "fehler": "API nicht erreichbar"}\n', encoding="utf-8")
    out.with_suffix(".lock").write_text("", encoding="utf-8")
    [L] = unfertige_laeufe(tmp_path)
    assert (L["name"], L["ziel"], L["fertig"], L["fehlgeschlagen"], L["gesperrt"]) == ("materialien_t.jsonl", 4, 2, 1, True)
    assert (L["verarbeitung"], L["modell"], L["limit"]) == ("flex", "m", 4)
    sperre_aufheben(out)
    assert not lauf_stand(out)["gesperrt"]
    out.write_text("".join(json.dumps({"id": f"s{i}"}) + "\n" for i in range(4)), encoding="utf-8")
    assert unfertige_laeufe(tmp_path) == []  # Ziel erreicht


def test_neu_starten_waehlt_freien_namen(tmp_path):
    out = tmp_path / "materialien_t.jsonl"
    out.write_text("", encoding="utf-8")
    assert freier_name(out).name == "materialien_t_2.jsonl"
    lauf_pfad(tmp_path / "materialien_t_2.jsonl").write_text("{}", encoding="utf-8")
    assert freier_name(out).name == "materialien_t_3.jsonl"


def test_fortsetzen_mit_flex_ueberschreibt_gespeicherte_verarbeitung(tmp_path, monkeypatch):
    import sem.__main__ as cli
    import sem.generate as gen

    seeds, out = _seeds(tmp_path, 3), tmp_path / "materialien_t.jsonl"
    lauf_speichern(out, seeds, LLMConfig(model="m"), limit=None, inhaltstyp_pruefen=True, ausgabesprache="de",
                   verarbeitung="sofort")
    gesehen = {}

    def generate(seeds_path, out_path, cfg, **kw):
        gesehen.update(service_tier=cfg.service_tier, seeds=seeds_path, out=out_path, model=cfg.model)
        return type("P", (), {"done": 0, "skipped": 3, "failed": 0, "status": "fertig"})()

    monkeypatch.setattr(gen, "generate", generate)
    monkeypatch.setattr(cli, "ausgabe_pfad", lambda art, name: out)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    cli.main(["materialien", "--fortsetzen", "t", "--flex"])
    assert gesehen["service_tier"] == "flex" and gesehen["model"] == "m"
    cli.main(["materialien", "--fortsetzen", "t"])
    assert gesehen["service_tier"] is None  # gespeicherte Verarbeitung „sofort“
