"""Anbindung externer Quellen, die mehrere Bildungsbereiche nutzen können."""

import urllib.error


def wiederholen(e: Exception) -> bool:
    """Netz- und Serverfehler sowie 429 lohnen eine Wiederholung, andere Client-Fehler (4xx) nicht."""
    return not (isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429)
