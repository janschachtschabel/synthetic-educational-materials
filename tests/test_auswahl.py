from sem.bereiche import BEREICHE
from sem.bereiche.base import STATUS_WERTE, filtergruppe


def test_jeder_bereich_hat_quellen_oder_konfidenz():
    for key, cls in BEREICHE.items():
        gruppen = {filtergruppe(n, o) for n, o in cls().auswahl_optionen().items()}
        assert "konfidenz" in gruppen, key
        assert "quellen" in gruppen, key


def test_konfidenz_setzt_alle_status_filter_ausser_explizite():
    b = BEREICHE["schule"]()
    wahl = b.mit_konfidenz({"stufen_status": STATUS_WERTE}, ["gesichert"])
    assert wahl["stufen_status"] == STATUS_WERTE
    assert wahl["art_status"] == ["gesichert"] and wahl["fach_status"] == ["gesichert"]
    assert "herkunft" not in wahl
    assert b.mit_konfidenz(None, None) == {}
