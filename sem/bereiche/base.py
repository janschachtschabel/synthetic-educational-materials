"""Schnittstelle für Bildungsbereiche.

Ein Bildungsbereich liefert Themen (mit Fachzuordnung), die fest codierten Metadaten
(Bildungsstufe), die wählbaren Felder für die Gleichverteilung und baut daraus Seeds.
Über auswahl_optionen() und passt() legt ein Bereich fest, nach welchen Kriterien (z. B. Status
der Zuordnung, Quelle, Belege) Themen ein- oder ausgeschlossen werden können.
Neue Bereiche (Schule, Berufsbildung, …) implementieren diese Klasse und registrieren sich
in sem/bereiche/__init__.py. Ihre Mappings bauen sie über mapping_schritte(); CLI und
Oberfläche zeigen diese Schritte ohne weitere Anpassung an.
"""

from __future__ import annotations

import random
import re
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from .. import schema as S
from ..llm import Progress
from ..paths import lokale_daten


# Namen für Personen in den Texten (gemischte Herkunft, wie in deutschen Lerngruppen); verhindert, dass das Modell
# immer dieselben Namen wählt (im 50k-Datensatz hieß das Kind im Elementarbereich in 231 Texten „Mila“)
VORNAMEN = ["Emma", "Noah", "Hannah", "Elias", "Lea", "Yusuf", "Aylin", "Jonas", "Mia", "Luca", "Amira", "Finn",
            "Sofia", "Mehmet", "Lina", "Paul", "Zeynep", "Ben", "Ida", "Mateo", "Nele", "Karim", "Maja", "Leon",
            "Fatma", "Theo", "Clara", "Emil", "Selin", "Anton", "Leni", "Milan", "Ronja", "David", "Elif", "Jakob",
            "Marie", "Samuel", "Ana", "Felix", "Nora", "Kai", "Lotta", "Oskar", "Jana", "Tim", "Aleksandra", "Malik",
            "Greta", "Ilias", "Paula", "Arda", "Frieda", "Jonte", "Sara", "Niklas", "Layla", "Moritz", "Zoe", "Can"]
NACHNAMEN = ["Becker", "Yılmaz", "Nowak", "Schmidt", "Hoffmann", "Kowalski", "Wagner", "Demir", "Fischer",
             "Popescu", "Krüger", "Haddad", "Weber", "Nguyen", "Lehmann", "Petrović", "Schulz", "Öztürk", "Braun",
             "Wolf", "Ivanova", "Richter", "Klein", "Hassan", "Neumann", "Kaya", "Zimmermann", "Schröder"]


@dataclass
class MappingSchritt:
    key: str
    label: str
    beschreibung: str
    # run(cfg, on_progress=None, stop_event=None, **optionen) -> Progress; unbekannte Optionen ignorieren
    run: Callable[..., Progress]
    llm: bool = True


def kurz_id(label: str) -> str:
    """Lesbare id für Prompts: „Lösungs(beispiel) und Erwartungshorizont“ → loesungsbeispiel_und_erwartungshorizont."""
    s = label.casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("(", ""), (")", "")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


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

    # Was eine Kombination ist (Oberfläche: Themen und mögliche Kombinationen je Auswahl)
    kombination_label: str = "Themen"

    def kombinationen(self, themen: list[dict], auswahl: dict[str, list[str]] | None = None) -> int:
        """Zahl der unterscheidbaren Thema-Varianten, aus denen Seeds gezogen werden; Standard: die Themen."""
        return len(themen)

    def alle_auswahl(self) -> dict[str, list[str]]:
        """Auswahl mit allen Werten jedes Filters: die größtmögliche Themenmenge. Belege sind Pflichtangaben
        (leer = keine verlangt) und bleiben deshalb leer."""
        return {k: [] if k == "belege" else list(o["werte"]) for k, o in self.auswahl_optionen().items()}

    def inhaltstyp_kategorien(self) -> list[dict]:
        """Kategorien für die Prüfung des Inhaltstyps am fertigen Text: [{"id", "name", "uri", "definition"}].
        Leer = keine Prüfung. Standard: die Inhaltstypen des Bereichs (inhaltstypen.json), Definition = Textsorte,
        also dieselbe Beschreibung, nach der der Volltext geschrieben wurde. Mehrfach genutzte Typen erscheinen einmal."""
        out: dict[str, dict] = {}
        for t in (getattr(self, "inhaltstypen", None) or {}).get("inhaltstypen", []):
            out.setdefault(t["uri"], {"id": kurz_id(t["label"]), "name": t["label"], "uri": t["uri"],
                                      "definition": t.get("textsorte", "")})
        return list(out.values())

    # Wortzahl des Volltexts, wenn der Inhaltstyp keinen eigenen Umfang hat
    umfang_standard: tuple[int, int] = (400, 550)
    # Einstiegsarten für den Volltext (eine je Seed, zufällig, aber fest zur Seed-ID)
    einstiege: tuple[str, ...] = (
        "mit einem konkreten Fallbeispiel", "mit einer Leitfrage oder einem Problem", "mit einer Alltagssituation",
        "mit einer kurzen Begriffsklärung", "mit einem überraschenden Befund oder Widerspruch",
        "mit einer Beobachtung oder einem Versuch", "direkt mit dem ersten inhaltlichen Punkt, ohne Vorrede")

    def gruppen_gewichte(self, balance: str, groups: dict[str, list[dict]]) -> dict[str, float] | None:
        """Gewichte der Gruppen für die Verteilung der Seeds; None = alle gleich (Standard)."""
        return None

    def umfang(self, seed: dict) -> tuple[int, int]:
        """Wortzahl (von, bis) des Volltexts: aus dem Inhaltstyp des Seeds (inhaltstypen.json, Feld umfang)."""
        uri = (seed.get(S.F_LRT) or [None])[0]
        for t in (getattr(self, "inhaltstypen", None) or {}).get("inhaltstypen", []):
            if t["uri"] == uri and t.get("umfang"):
                return int(t["umfang"][0]), int(t["umfang"][1])
        return self.umfang_standard

    def stil(self, seed_id: str) -> dict:
        """Stilvorgaben gegen gleichförmige Texte: Einstiegsart und Namen für Personen im Text. Fest zur Seed-ID,
        damit ein Neuaufbau dieselben Vorgaben ergibt und die übrigen Zufallswerte des Seeds unberührt bleiben."""
        rng = random.Random(f"stil:{seed_id}")
        vornamen = rng.sample(VORNAMEN, 2)
        return {"einstieg": rng.choice(self.einstiege),
                "namen": [*vornamen, f"{rng.choice(['Frau', 'Herr'])} {rng.choice(NACHNAMEN)}"]}

    # benannte Schrittfolgen, z. B. {"openalex": [...], "gnd": [...]}; „alle“ = alle nacheinander
    mapping_folgen: dict[str, list[str]] = {}
    # Schritte, die nach jeder Auswahl zuletzt laufen (z. B. Teilergebnisse zum Gesamtset zusammenführen)
    mapping_abschluss: list[str] = []

    @property
    def mapping_folge(self) -> list[str]:
        return self._mit_abschluss([k for f in self.mapping_folgen.values() for k in f])

    def _mit_abschluss(self, keys: list[str]) -> list[str]:
        keys = [k for k in dict.fromkeys(keys) if k not in self.mapping_abschluss]
        return keys + list(self.mapping_abschluss) if keys else list(self.mapping_abschluss)

    def schritte_aufloesen(self, namen: list[str]) -> list[str]:
        """Expandiert „alle“ und Folgennamen zu Schritten; Abschlussschritte kommen ans Ende."""
        out = []
        for n in namen:
            out += self.mapping_folge if n == "alle" else self.mapping_folgen.get(n, [n])
        return self._mit_abschluss(out)

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
            ok = prog.status.startswith("fertig")  # vor dem Präfix prüfen: Labels können „: “ enthalten
            prog.status = f"{schritte[k].label}: {prog.status}"
            if on_progress:
                on_progress(prog)
            if not ok:
                break
        return prog

    def mapping_statistik(self) -> dict:
        """Kennzahlen zur Güte der Mappings (z. B. Anteil gesicherter Zuordnungen). Standard: keine."""
        return {}

    def auswahl_optionen(self) -> dict[str, dict]:
        """Wählbare Filter für die Themen, z. B. nach Status oder Quelle:
        {name: {"label", "beschreibung", "werte": {wert: beschreibung}, "standard": [werte]}}.
        Standard: keine (alle Themen)."""
        return {}

    def standard_auswahl(self) -> dict[str, list[str]]:
        return {k: list(o["standard"]) for k, o in self.auswahl_optionen().items()}

    def mit_konfidenz(self, auswahl: dict[str, list[str]] | None, konfidenz: list[str] | None) -> dict[str, list[str]]:
        """Setzt alle Status-Filter (Konfidenz) auf dieselben Werte, soweit auswahl sie nicht ausdrücklich setzt."""
        out = dict(auswahl or {})
        if konfidenz is not None:
            for k, o in self.auswahl_optionen().items():
                if filtergruppe(k, o) == "konfidenz" and k not in out:
                    out[k] = list(konfidenz)
        return out

    def passt(self, topic: dict, auswahl: dict[str, list[str]]) -> bool:
        """Ob ein Thema zur Auswahl passt. Standard: immer."""
        return True

    def auswahl_pruefen(self, auswahl: dict[str, list[str]] | None) -> dict[str, list[str]]:
        """Ergänzt fehlende Filter mit ihrem Standard und weist unbekannte Werte zurück."""
        opts = self.auswahl_optionen()
        out = self.standard_auswahl()
        for k, werte in (auswahl or {}).items():
            if k not in opts:
                raise ValueError(f"Unbekannter Filter {k!r} für {self.key}. Möglich: {', '.join(opts) or 'keine'}")
            falsch = [w for w in werte if w not in opts[k]["werte"]]
            if falsch:
                raise ValueError(f"Unbekannte Werte {falsch} für {k}. Möglich: {', '.join(opts[k]['werte'])}")
            out[k] = list(werte)
        return out

    def themen_auswahl(self, auswahl: dict[str, list[str]] | None = None) -> list[dict]:
        auswahl = self.auswahl_pruefen(auswahl)
        return [t for t in self.topics() if self.passt(t, auswahl)]

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "balance_options": self.balance_options,
                "default_balance": self.default_balance, "themen": len(self.topics()),
                "themen_standardauswahl": len(self.themen_auswahl()), "auswahl_optionen": self.auswahl_optionen(),
                "mapping_schritte": [s.key for s in self.mapping_schritte()]}


STATUS_WERTE = ["gesichert", "wahrscheinlich", "unsicher", "redaktionell"]
STANDARD_KONFIDENZ = ["gesichert", "wahrscheinlich", "redaktionell"]


def filtergruppe(name: str, option: dict) -> str:
    """Gruppe eines Filters für Oberfläche und CLI: „quellen“ (Quellen, Herkunft, Belege), „konfidenz“
    (Status einer Zuordnung aus der Abstimmung) oder „inhalt“ (alles andere, z. B. Stufen, Themenart)."""
    if name in ("quellen", "herkunft", "belege"):
        return "quellen"
    if set(option["werte"]) == set(STATUS_WERTE):
        return "konfidenz"
    return "inhalt"


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


def allocate(n: int, groups: dict[str, list[dict]], rng: random.Random,
             gewichte: dict[str, float] | None = None) -> list[tuple[str, dict]]:
    """Verteilt n Seeds möglichst gleich auf die Gruppen, mit gewichte im Verhältnis der Gewichte.

    Innerhalb einer Gruppe kommen alle Themen einmal dran (zufällige Reihenfolge), bevor sich
    eines wiederholt. Ist n kleiner als die Zahl der Gruppen, werden n Gruppen zufällig gewählt.
    """
    keys = sorted(groups)
    rng.shuffle(keys)
    if gewichte:
        # größte Reste: ganzzahlige Anteile, Rest an die Gruppen mit dem größten Nachkommaanteil
        summe = sum(gewichte[k] for k in keys)
        roh = {k: n * gewichte[k] / summe for k in keys}
        quota = {k: int(roh[k]) for k in keys}
        for k in sorted(keys, key=lambda k: roh[k] - quota[k], reverse=True)[:n - sum(quota.values())]:
            quota[k] += 1
    else:
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


_URL = re.compile(r"\s*[(\[]?\s*(?:https?://|www\.)[^\s)\]]*[^\s)\].,;:]\s*[)\]]?")


def ohne_urls(text: str) -> str:
    """Entfernt Web-Adressen (samt umschließender Klammer) aus einer Themenbeschreibung, z. B. Quellenverweise
    in Definitionen des WLO-Themenbaums; sie gehören nicht in Prompt und Seed."""
    return re.sub(r"\s+([.,;:])", r"\1", _URL.sub(" ", text)).strip()


def build_seeds(bereich: Bildungsbereich, n: int, balance: str | None = None, seed: int = 42,
                id_prefix: str | None = None, auswahl: dict[str, list[str]] | None = None) -> list[dict]:
    balance = balance or bereich.default_balance
    if balance not in bereich.balance_options:
        raise ValueError(f"Unbekanntes Gleichverteilungsfeld {balance!r} für {bereich.key}. "
                         f"Möglich: {', '.join(bereich.balance_options)}")
    rng = random.Random(seed)
    auswahl = bereich.auswahl_pruefen(auswahl)
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in bereich.themen_auswahl(auswahl):
        groups[bereich.balance_key(t, balance)].append(t)
    if not groups:
        raise ValueError(f"Keine Themen für {bereich.key} mit dieser Auswahl: {auswahl}")
    state = SeedState()
    seeds = []
    prefix = id_prefix or bereich.key
    # Kennung der lokalen Dateien des Bereichs: gleicher Startwert ergibt nur bei gleicher Kennung dieselben Seeds
    lokal = lokale_daten(bereich.key)
    gewichte = bereich.gruppen_gewichte(balance, groups)
    for i, (group, topic) in enumerate(allocate(n, groups, rng, gewichte), 1):
        state.group = group
        s = {"id": f"{prefix}-{seed}-{i:06d}"}
        s.update(bereich.make_seed(topic, rng, state))
        thema = s.get(S.F_THEMA) or {}
        if thema.get("beschreibung_de"):
            thema["beschreibung_de"] = ohne_urls(thema["beschreibung_de"])
        # aus gnd_ergaenzungen.jsonl: Herkunft einer ergänzten Beschreibung, ersetzter Motiv-Satz
        for feld in ("beschreibung_quelle", "gnd_motiv"):
            if thema and topic.get(feld):
                thema[feld] = topic[feld]
        von, bis = bereich.umfang(s)
        s[S.F_UMFANG] = {"von": von, "bis": bis}
        s[S.F_STIL] = bereich.stil(s["id"])
        s["synth:seed_parameter"] = {"gleichverteilung": balance, "gruppe": group, "zufalls_seed": seed,
                                     "auswahl": auswahl, **({"lokale_daten": lokal} if lokal else {})}
        seeds.append(s)
    return seeds
