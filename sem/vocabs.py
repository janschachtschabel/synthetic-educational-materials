"""Kontrollierte Vokabulare: Laden aus SKOS-Quellen und aus den aufbereiteten JSON-Dateien.

Jede aufbereitete Datei unter data/vocabs/ hat dieselbe Form:

    {"scheme": {...Herkunft...}, "concepts": [{"uri", "notation", "label", "broader", "level"}, ...]}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from .paths import VOCAB_DIR

OEH_VOCABS_RAW = "https://raw.githubusercontent.com/openeduhub/oeh-metadata-vocabs/master/{name}.ttl"
OEH_VOCABS_PUBLIC = "https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/{name}/"

# Vokabulare, die das Repo aufbereitet mitliefert (Dateiname = OEH-Vokabularname)
OEH_VOCABS = ["educationalContext", "new_lrt", "hochschulfaechersystematik", "intendedEndUserRole"]


@dataclass
class Vocab:
    name: str
    scheme: dict
    concepts: list[dict]
    by_uri: dict[str, dict] = field(init=False)

    def __post_init__(self):
        self.by_uri = {c["uri"]: c for c in self.concepts}

    def ref(self, uri: str) -> dict:
        """Kurzreferenz für ein Konzept: URI, Label, Notation (falls vorhanden)."""
        c = self.by_uri[uri]
        out = {"uri": uri, "label": c["label"]}
        if c.get("notation"):
            out["notation"] = c["notation"]
        return out

    def children(self, uri: str) -> list[dict]:
        return [c for c in self.concepts if c.get("broader") == uri]

    def ancestors(self, uri: str) -> list[str]:
        out = []
        while (b := self.by_uri[uri].get("broader")):
            out.append(b)
            uri = b
        return out


@lru_cache(maxsize=None)
def load(name: str) -> Vocab:
    data = json.loads((VOCAB_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return Vocab(name, data["scheme"], data["concepts"])


def skos_to_json(ttl_path: Path, name: str, source_url: str, lang: str = "de") -> dict:
    """Wandelt ein SKOS-Turtle-Vokabular in die flache JSON-Form des Repos um."""
    import rdflib
    from rdflib.namespace import DCTERMS, SKOS

    g = rdflib.Graph()
    g.parse(ttl_path)
    scheme = next(g.subjects(rdflib.RDF.type, SKOS.ConceptScheme))

    def label(s):
        labels = {o.language: str(o) for o in g.objects(s, SKOS.prefLabel)}
        return labels.get(lang) or labels.get("en") or next(iter(labels.values()), None)

    concepts = {}
    for s in g.subjects(rdflib.RDF.type, SKOS.Concept):
        concepts[str(s)] = {
            "uri": str(s),
            "notation": str(g.value(s, SKOS.notation) or "") or None,
            "label": label(s),
            "broader": None,
        }
    # Hierarchie aus broader und narrower zusammenführen (die Quellen sind nicht immer symmetrisch)
    for s, o in g.subject_objects(SKOS.broader):
        if str(s) in concepts:
            concepts[str(s)]["broader"] = str(o) if str(o) in concepts else None
    for s, o in g.subject_objects(SKOS.narrower):
        if str(o) in concepts and not concepts[str(o)]["broader"] and str(s) in concepts:
            concepts[str(o)]["broader"] = str(s)
    for c in concepts.values():
        level, b = 0, c["broader"]
        while b:
            level, b = level + 1, concepts[b]["broader"]
        c["level"] = level
        if c["notation"] is None:
            del c["notation"]

    titles = {o.language: str(o) for o in g.objects(scheme, DCTERMS.title)}
    return {
        "scheme": {
            "name": name,
            "uri": str(scheme),
            "title": titles.get(lang) or titles.get("en"),
            "source": source_url,
            "public_url": OEH_VOCABS_PUBLIC.format(name=name),
            "license": "CC0-1.0",
            "retrieved": date.today().isoformat(),
        },
        "concepts": sorted(concepts.values(), key=lambda c: (c["level"], c.get("notation") or "", c["label"] or "")),
    }
