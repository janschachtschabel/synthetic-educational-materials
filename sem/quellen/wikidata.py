"""Wikidata (CC0): kurze deutsche Beschreibungen zu GND-Sätzen über die GND-ID (Eigenschaft P227).

Nur die Wikidata-Beschreibung („Hauptstadt der Niederlande“), keine Wikipedia-Texte (CC BY-SA). Antworten werden wie
bei lobid in data/cache/lobid/ zwischengespeichert.
"""

from __future__ import annotations

from .gnd import _get

SPARQL = "https://query.wikidata.org/sparql"


def beschreibungen(gnd_ids: list[str], batch: int = 150) -> dict[str, dict]:
    """GND-ID → {"wikidata": Q-ID, "beschreibung": deutsche Beschreibung} (nur Treffer mit Beschreibung)."""
    out: dict[str, dict] = {}
    ids = sorted({i for i in gnd_ids if i and '"' not in i})
    for k in range(0, len(ids), batch):
        werte = " ".join(f'"{i}"' for i in ids[k:k + batch])
        q = ("SELECT ?gnd ?item ?desc WHERE { VALUES ?gnd { " + werte + " } ?item wdt:P227 ?gnd . "
             '?item schema:description ?desc . FILTER(LANG(?desc) = "de") }')
        data = _get(SPARQL, {"query": q, "format": "json"}, pause=0.5)
        for b in data.get("results", {}).get("bindings", []):
            g, desc = b["gnd"]["value"], b["desc"]["value"].strip()
            if desc and g not in out:
                out[g] = {"wikidata": b["item"]["value"].rsplit("/", 1)[-1], "beschreibung": desc}
    return out
