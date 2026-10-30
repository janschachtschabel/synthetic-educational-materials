import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from sem import paths  # noqa: E402


def lokal_vorhanden(name: str) -> bool:
    """Ob eine Datei unter data/lokal/ liegt (für skipif in Tests, die die lokalen Daten brauchen)."""
    return paths.lokal_pfad(name).exists()


@pytest.fixture
def ohne_lokal(monkeypatch, tmp_path):
    """Lenkt data/lokal/ auf einen leeren Ordner um, wie in einem frischen Klon."""
    from sem.bereiche.schule import bildungsstandards

    monkeypatch.setattr(paths, "LOKAL_DIR", tmp_path / "lokal")
    bildungsstandards.gliederung.cache_clear()
    yield tmp_path / "lokal"
    bildungsstandards.gliederung.cache_clear()
