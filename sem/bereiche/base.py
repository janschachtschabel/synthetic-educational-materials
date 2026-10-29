"""Schnittstelle für Bildungsbereiche.

Ein Bildungsbereich liefert Themen (mit Fachzuordnung), die fest codierten Metadaten
(Bildungsstufe), die wählbaren Felder für die Gleichverteilung und baut daraus Seeds.
Neue Bereiche (Schule, Berufsbildung, …) implementieren diese Klasse und registrieren sich
in sem/bereiche/__init__.py. Ihre Mappings bauen sie über mapping_schritte(); CLI und
Oberfläche zeigen diese Schritte ohne weitere Anpassung an.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from ..llm import Progress


@dataclass
class MappingSchritt:
    key: str
    label: str
    beschreibung: str
    # run(cfg, on_progress=None, stop_event=None, **optionen) -> Progress; unbekannte Optionen ignorieren
    run: Callable[..., Progress]
    llm: bool = True


def fertig(text: str) -> Progress:
    """Progress für Schritte ohne LLM."""
    return Progress(total=1, done=1, status=f"fertig – {text}")


class Bildungsbereich(ABC):
    key: str
    label: str
    # Feld für die Gleichverteilung -> Beschreibung (für UI und CLI)
    balance_options: dict[str, str]
    default_balance: str

    @abstractmethod
    def topics(self) -> list[dict]:
        """Alle Themen des Bereichs. Jedes Thema hat mindestens eine eindeutige "id"."""

    @abstractmethod
    def balance_key(self, topic: dict, balance: str) -> str:
        """Gruppenschlüssel eines Themas für das gewählte Gleichverteilungsfeld."""

    @abstractmethod
    def make_seed(self, topic: dict, rng: random.Random, state: "SeedState") -> dict:
        """Baut einen Seed zu einem Thema. state hält Zähler für rotierende Felder."""

    @abstractmethod
    def prompt_values(self, seed: dict) -> dict:
        """Werte für die Platzhalter der Prompt-Vorlagen aus einem Seed."""

    # benannte Schrittfolgen, z. B. {"openalex": [...], "gnd": [...]}; „alle“ = alle nacheinander
    mapping_folgen: dict[str, list[str]] = {}

    @property
    def mapping_folge(self) -> list[str]:
        return list(dict.fromkeys(k for f in self.mapping_folgen.values() for k in f))

    def schritte_aufloesen(self, namen: list[str]) -> list[str]:
        """Expandiert „alle“ und Folgennamen zu Schritten."""
        out = []
        for n in namen:
            out += self.mapping_folge if n == "alle" else self.mapping_folgen.get(n, [n])
        return list(dict.fromkeys(out))

    def mapping_schritte(self) -> list[MappingSchritt]:
        """Schritte, mit denen der Bereich seine Mappings neu baut. Standard: keine."""
        return []

    def mapping_ausfuehren(self, keys: list[str], cfg, on_progress=None, stop_event=None, **optionen) -> Progress:
        """Führt Schritte nacheinander aus und hält an, sobald einer nicht fertig wird."""
        schritte = {s.key: s for s in self.mapping_schritte()}
        prog = fertig("nichts zu tun")
        for k in keys:
            if stop_event is not None and stop_event.is_set():
                break
            prog = schritte[k].run(cfg, on_progress=on_progress, stop_event=stop_event, **optionen)
            prog.status = f"{schritte[k].label}: {prog.status}"
            if on_progress:
                on_progress(prog)
            if not prog.status.split(": ", 1)[1].startswith("fertig"):
                break
        return prog

    def mapping_statistik(self) -> dict:
        """Kennzahlen zur Güte der Mappings (z. B. Anteil gesicherter Zuordnungen). Standard: keine."""
        return {}

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "balance_options": self.balance_options,
                "default_balance": self.default_balance, "themen": len(self.topics()),
                "mapping_schritte": [s.key for s in self.mapping_schritte()]}


class SeedState:
    """Zähler für rotierende Zuweisungen, z. B. „seltenster passender Inhaltstyp je Gruppe“."""

    def __init__(self):
        self.counts: dict[str, Counter] = defaultdict(Counter)
        self.group: str | None = None

    def pick_least_used(self, scope: str, options: list[str], rng: random.Random) -> str:
        c = self.counts[scope]
        low = min(c[o] for o in options)
        choice = rng.choice([o for o in options if c[o] == low])
        c[choice] += 1
        return choice


def allocate(n: int, groups: dict[str, list[dict]], rng: random.Random) -> list[tuple[str, dict]]:
    """Verteilt n Seeds möglichst gleich auf die Gruppen.

    Innerhalb einer Gruppe kommen alle Themen einmal dran (zufällige Reihenfolge), bevor sich
    eines wiederholt. Ist n kleiner als die Zahl der Gruppen, werden n Gruppen zufällig gewählt.
    """
    keys = sorted(groups)
    rng.shuffle(keys)
    base, rest = divmod(n, len(keys))
    quota = {k: base + (1 if i < rest else 0) for i, k in enumerate(keys)}
    out = []
    for k in keys:
        topics = groups[k]
        cycle: list[dict] = []
        for _ in range(quota[k]):
            if not cycle:
                cycle = topics[:]
                rng.shuffle(cycle)
            out.append((k, cycle.pop()))
    rng.shuffle(out)
    return out


def build_seeds(bereich: Bildungsbereich, n: int, balance: str | None = None, seed: int = 42,
                id_prefix: str | None = None) -> list[dict]:
    balance = balance or bereich.default_balance
    if balance not in bereich.balance_options:
        raise ValueError(f"Unbekanntes Gleichverteilungsfeld {balance!r} für {bereich.key}. "
                         f"Möglich: {', '.join(bereich.balance_options)}")
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in bereich.topics():
        groups[bereich.balance_key(t, balance)].append(t)
    state = SeedState()
    seeds = []
    prefix = id_prefix or bereich.key
    for i, (group, topic) in enumerate(allocate(n, groups, rng), 1):
        state.group = group
        s = {"id": f"{prefix}-{seed}-{i:06d}"}
        s.update(bereich.make_seed(topic, rng, state))
        s["synth:seed_parameter"] = {"gleichverteilung": balance, "gruppe": group, "zufalls_seed": seed}
        seeds.append(s)
    return seeds
