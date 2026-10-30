"""Registry der Bildungsbereiche. Ein neuer Bereich wird hier eingetragen."""

from .base import Bildungsbereich
from .berufsbildung import Berufsbildung
from .elementar import Elementar
from .erwachsenenbildung import Erwachsenenbildung
from .hochschule import Hochschule
from .schule import Schule

BEREICHE: dict[str, type[Bildungsbereich]] = {
    Hochschule.key: Hochschule,
    Berufsbildung.key: Berufsbildung,
    Elementar.key: Elementar,
    Schule.key: Schule,
    Erwachsenenbildung.key: Erwachsenenbildung,
}


def get(key: str) -> Bildungsbereich:
    try:
        return BEREICHE[key]()
    except KeyError:
        raise KeyError(f"Unbekannter Bildungsbereich {key!r}. Verfügbar: {', '.join(BEREICHE)}") from None
