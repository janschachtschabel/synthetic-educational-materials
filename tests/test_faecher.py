import json

from sem import faecher
from sem import schema as S
from sem.bereiche import get
from sem.bereiche.base import build_seeds
from sem.vocabs import load as load_vocab

D = "http://w3id.org/openeduhub/vocabs/discipline/"


def test_mappings_zeigen_auf_gueltige_faecher():
    voc = load_vocab("discipline")
    for datei in (faecher.KLDB_DATEI, faecher.EB_DATEI):
        m = json.loads(datei.read_text(encoding="utf-8"))
        uris = [u for v in m["closeMatch"].values() for u in v] + m.get("immer", []) + m.get("ersatz", [])
        assert uris and all(u in voc.by_uri for u in uris)


def test_berufsbildung_immer_berufliche_bildung_plus_hauptgruppe():
    assert faecher.fach_uris_berufsbildung(["http://w3id.org/openeduhub/vocabs/kldb/4341"]) == [D + "040", D + "320"]
    assert faecher.fach_uris_berufsbildung(["http://w3id.org/openeduhub/vocabs/kldb/5410"]) == [D + "040"]


def test_erwachsenenbildung_fachgebiet_sonst_weiterbildung():
    assert faecher.fach_uris_erwachsenenbildung(["4.06", "4.08"]) == [D + "20001", D + "20002"]
    assert faecher.fach_uris_erwachsenenbildung(["6.02"]) == [D + "680"]


def test_setzen_laesst_vorhandenes_fach_und_andere_bereiche():
    rec = {S.F_BEREICH: "erwachsenenbildung",
           "synth:fachgebiet_vhs": ["https://example.org/programmbereiche.json#fg3.02"]}
    assert faecher.setzen(rec) and rec[S.F_TAXON] == [D + "600"] and rec[S.F_TAXON + "_DISPLAYNAME"] == ["Sport"]
    assert not faecher.setzen(rec)
    assert not faecher.setzen({S.F_BEREICH: "hochschule"})


def test_ergaenzen_schreibt_neue_datei(tmp_path):
    ein, aus = tmp_path / "m.jsonl", tmp_path / "m_faecher.jsonl"
    zeilen = [{"id": "a", S.F_BEREICH: "berufsbildung", S.F_PROFESSION: ["http://w3id.org/openeduhub/vocabs/kldb/8121"]},
              {"id": "b", S.F_BEREICH: "schule", S.F_TAXON: [D + "380"]}]
    ein.write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
    assert faecher.ergaenzen(ein, aus) == {"gesamt": 2, "ergaenzt": 1, "je_bereich": {"berufsbildung": 1}}
    out = [json.loads(z) for z in aus.read_text(encoding="utf-8").splitlines()]
    assert out[0][S.F_TAXON] == [D + "040", D + "260"] and out[1] == zeilen[1]
    assert "nachträglich" in out[0][S.F_PROVENANCE][S.F_TAXON]["methode"]


def test_seeds_berufs_und_erwachsenenbildung_haben_fach():
    for key in ("berufsbildung", "erwachsenenbildung"):
        b = get(key)
        try:
            b.topics()
        except FileNotFoundError:
            continue
        for s in build_seeds(b, 50, seed=1):
            assert s[S.F_TAXON] and len(s[S.F_TAXON]) == len(s[S.F_TAXON + "_DISPLAYNAME"])
