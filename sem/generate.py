"""Erzeugt synthetische Bildungsmaterialien aus Seeds.

Pro Seed nacheinander:
  1. Volltext aus dem Seed (volltext_system + volltext)
  2. Titel, Beschreibung, Keywords: je ein Aufruf mit gemeinsamem Systemprompt (system) und
     gemeinsamer Materialvorlage (material); der Auftrag kommt aus der Felddatei. Jeder Aufruf
     sieht den Volltext, die festen Metadaten des Seeds und die schon erzeugten Felder.
  3. gleichzeitig: Inhaltstyp-Prüfung (der Inhaltstyp wird ohne Kenntnis des Seed-Werts aus dem
     fertigen Material bestimmt und mit dem Seed verglichen, nur protokolliert) und
     Frage-Antwort-Paare (JSON)
Liefert ein Feld nichts Brauchbares, wird nur dieses Feld erneut angefragt. Dauer und
Neuanfragen je Feld stehen in synth:generierung.

Material hält den Stand eines Materials über diese Stufen. generate_one arbeitet sie sofort ab
(ein Aufruf nach dem anderen), sem.batch über die Batch-Schnittstelle in Runden; Prompts, Prüfungen und
Ergebnis sind in beiden Fällen gleich.

Ausgabesprache: Standard Deutsch. Bei einer anderen Sprache wird prompts/ausgabesprache.md an beide
Systemprompts angehängt; die Vorgaben bleiben deutsch, cclom:general_language erhält die Ausgabesprache.
"""

from __future__ import annotations

import json
import hashlib
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import schema as S
from .bereiche import get as get_bereich
from .llm import TOKEN_FELDER, LLMClient, LLMConfig, Progress, json_aus_text, print_progress, run_tasks
from .prompts import load_prompts, prompt_hash, render

LEER = "-"
FELD_VERSUCHE = 3
MIN_WOERTER = 60  # Volltext: absolute Untergrenze; die eigentliche Grenze hängt vom Umfang des Inhaltstyps ab
MIN_ANTEIL_UMFANG = 0.6  # kürzer als 60 % der unteren Umfangsgrenze: neu anfragen
# Stufen eines Materials; die Felder einer Stufe hängen nicht voneinander ab und laufen gleichzeitig
STUFEN = [["volltext"], ["titel"], ["beschreibung"], ["keywords"], ["inhaltstyp", "fragen_antworten"]]
# Ausgabesprachen (ISO 639-1 → Name im Prompt); andere Codes werden unverändert übergeben
SPRACHEN = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch", "es": "Spanisch", "it": "Italienisch",
            "nl": "Niederländisch", "pl": "Polnisch", "tr": "Türkisch", "uk": "Ukrainisch", "ru": "Russisch",
            "ar": "Arabisch"}
_QUOTES = "\"'„“”«»‚‘’`"


def read_seeds(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# --- Prüfen der Antworten ---------------------------------------------------------------------

def _einzeilig(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(_QUOTES).strip()


TITEL_MAX = 80  # Prompt verlangt höchstens 60 Zeichen; etwas Spielraum, damit nicht jeder Ausreißer neu angefragt wird
# Untertitel-Doppelpunkt („Wechselstrom: …“) und Gedankenstrich; der Gender-Doppelpunkt im Wort
# („Patient:innen“) ist erlaubt
_TITEL_TRENNER = re.compile(r":(?![a-zäöüß])|\s[–—-]\s")
_TITEL_REST = re.compile(r"\s[a-zà-ÿ]{1,2}$")  # abgerissenes Kurzwort am Ende, z. B. „… formale Sprachen bg“
_TITEL_WORTENDE = {"zu", "an", "um", "ab", "in", "im", "am"}  # echte Wörter, die am Ende stehen dürfen


def _fremdes_zeichen(t: str) -> str | None:
    """Erstes Zeichen außerhalb lateinischer/griechischer Schrift, Ziffern und üblicher Satzzeichen."""
    for c in t:
        # Fachzeichen (§ 266 StGB, 90 °C, Bose–Einstein, „Faust“, E = mc², µm) sind erlaubt; Gedankenstriche
        # mit Leerzeichen fängt _TITEL_TRENNER vorher ab
        if c.isspace() or c.isdigit() or c in "-‐‑–,.?!&/+'’%()§°„“‚‘=×·µ":
            continue
        name = unicodedata.name(c, "")
        if not name.startswith(("LATIN", "GREEK")):
            return c
    return None


def parse_titel(text: str) -> str:
    t = _einzeilig(_einzeilig(text.strip().splitlines()[0] if text.strip() else "").rstrip("."))
    if t.count("„") > t.count("“"):  # _einzeilig nimmt ein schließendes Zitatzeichen am Ende mit: „Faust“
        t += "“"
    if not 10 <= len(t) <= TITEL_MAX:
        raise ValueError(f"Titel mit {len(t)} Zeichen")
    if _TITEL_TRENNER.search(t):
        raise ValueError("Titel mit Doppelpunkt oder Gedankenstrich")
    if c := _fremdes_zeichen(t):
        raise ValueError(f"Titel mit fremdem Zeichen {c!r}")
    if (m := _TITEL_REST.search(t)) and m.group().strip() not in _TITEL_WORTENDE:
        raise ValueError(f"Titel endet auf {m.group().strip()!r}")
    if verb := next((w for w in re.findall(r"[^\W\d_]+", t) if w in _TITEL_FUELLVERBEN), None):
        raise ValueError(f"Titel mit Füllverb {verb!r}")
    return t


# Lernziel-Verben, die der Prompt ausschließt (kleingeschrieben = Verb, nicht das Substantiv am Titelanfang)
_TITEL_FUELLVERBEN = {"verstehen", "entdecken", "kennenlernen", "erkunden", "erfahren", "erarbeiten"}
# Bezeichnungen für Materialtyp und Stufe, die nicht in den Titel gehören. Sie zählen nur, wenn sie auch in den
# Metadaten des Materials stehen (dann übernimmt das Modell sie von dort) und nicht zum Thema gehören.
_FORM_WOERTER = {
    "arbeitsblatt", "übungsblatt", "fingerspiel", "bewegungsreim", "reim", "lied", "bewegungslied", "quiz", "video",
    "erklärvideo", "podcast", "podcastfolge", "checkliste", "tafelbild", "skript", "handout", "lernauftrag",
    "arbeitsauftrag", "rechercheauftrag", "selbsttest", "präsentation", "merksatz", "stundenentwurf",
    "unterrichtsentwurf", "interview", "anleitung", "kursbeschreibung", "lernspiel", "steckbrief",
    "kita", "krippe", "kindergarten", "grundschule", "sekundarstufe", "oberstufe", "klasse", "studierende",
    "bachelor", "master", "auszubildende", "ausbildungsjahr", "berufsschule",
}
_FORM_ENDUNGEN = ("reim", "video", "podcast", "blatt")  # auch als Wortende: Kinderreim, Lernvideo, Übungsblatt
_FORM_FELDER = ("textsorte", "inhaltstyp", "medienform", "bildungsstufe", "niveau", "zielgruppe", "lernort",
                "sozialform")
_THEMA_FELDER = ("thema", "thema_keywords", "thema_pfad")


def titel_nennt_form(titel: str, values: dict) -> str | None:
    """Wort des Titels, das Materialtyp oder Stufe aus den Metadaten wiederholt (z. B. „Bewegungsreim zum
    Motorrad“ bei Textsorte „Lied, Fingerspiel oder Reim“), sonst None."""
    form = " ".join(str(values.get(k) or "") for k in _FORM_FELDER).casefold()
    thema = " ".join(str(values.get(k) or "") for k in _THEMA_FELDER).casefold()
    for wort in re.findall(r"[^\W\d_]+", titel.casefold()):
        kandidaten = ([wort] if wort in _FORM_WOERTER else []) + [e for e in _FORM_ENDUNGEN if wort.endswith(e)]
        if any(k in form and k not in thema for k in kandidaten):
            return wort
    return None


BESCHREIBUNG_MIN, BESCHREIBUNG_MAX = 300, 1000  # Prompt: 450–900 Zeichen; etwas Spielraum gegen unnötige Neuanfragen
# Schablonenhafte Anfänge, die der Prompt ausschließt (im 50k-Datensatz begannen 37 % mit „Im Mittelpunkt“, 18 %
# mit „Das Material“)
_BESCHREIBUNG_FLOSKEL = re.compile(
    r"^(im mittelpunkt|das material|dieses material|das vorliegende|in diesem|in dieser|hier |dieser |diese |dieses )",
    re.I)


# dieselbe Schablone, nur ans Satzende verschoben („… steht im Mittelpunkt“, „… wird behandelt“)
_BESCHREIBUNG_FLOSKEL_SATZ = re.compile(
    r"\b(?:steh(?:t|en)|rück(?:t|en)) (?:hier )?im (?:mittelpunkt|zentrum|fokus|vordergrund)\b"
    r"|\b(?:ist|sind) (?:das |die )?(?:zentrale[sn]? |eigentliche[sn]? )?(?:thema|themen|gegenstand)\b"
    r"|\bim (?:fokus|zentrum|mittelpunkt) (?:steh|lieg)"
    r"|\b(?:wird|werden)\b[^.!?]*\b(?:behandelt|thematisiert|vorgestellt|beleuchtet|betrachtet|erörtert|diskutiert|aufgegriffen)\b"
    r"|\bgeht es um\b"
    # Leerformeln statt Aussage („… stehen als Themen nebeneinander“, „… gehören zum Themenfeld“)
    r"|\bals themen\b|\bzum themenfeld\b|\bsind gemeint\b|\bbilde[nt] (?:zwei |die |den |das )?(?:gegenst|themen?\b|fall\b|rahmen\b|kern\b|ausgangspunkt|\w*aufgabe)",
    re.I)
_ERSTER_SATZ = re.compile(r"^.+?[.!?](?=\s+[„\"(]?[A-ZÄÖÜ0-9]|\s*$)", re.S)


# Satzform des ersten Beschreibungssatzes je Material (aus der id reproduzierbar), damit sich keine
# Lieblingsform einschleift; genutzt bei der Erzeugung und von beschreibung_einstieg
SATZFORMEN = (
    "eine Leitfrage, die der Text beantwortet",
    "den zentralen Fachbegriff als Satzgegenstand mit einem Verb, das eine Aussage aus dem bisherigen Satz trägt",
    "die Situation, das Problem oder die Aufgabe, um die es geht, als Aussagesatz",
)


def satzform(rec_id: str) -> str:
    return SATZFORMEN[int(hashlib.sha1(rec_id.encode()).hexdigest(), 16) % len(SATZFORMEN)]


def beschreibung_floskel(text: str) -> str | None:
    """Schablone im ersten Satz der Beschreibung: am Anfang („Im Mittelpunkt …“, „Das Material …“) oder als
    Satzende („… steht im Mittelpunkt“, „… wird behandelt“); sonst None."""
    t = text.strip()
    if m := _BESCHREIBUNG_FLOSKEL.match(t):
        return m.group().strip()
    erster = (m.group() if (m := _ERSTER_SATZ.match(t)) else t)
    return m.group().strip() if (m := _BESCHREIBUNG_FLOSKEL_SATZ.search(erster)) else None


def parse_beschreibung(text: str) -> str:
    t = _einzeilig(text)
    if not BESCHREIBUNG_MIN <= len(t) <= BESCHREIBUNG_MAX:
        raise ValueError(f"Beschreibung mit {len(t)} Zeichen")
    return t


def parse_keywords(text: str, minimum: int = 5, maximum: int = 8) -> list[str]:
    roh = [_einzeilig(k.lstrip("-•*0123456789. ")) for k in re.split(r"[,;\n]", text)]
    out: dict[str, str] = {}
    for k in roh:
        if k and len(k) <= 60:
            out.setdefault(k.casefold(), k)
    if len(out) < minimum:
        raise ValueError(f"{len(out)} Keywords")
    return list(out.values())[:maximum]


def parse_inhaltstyp(text: str, kategorien: list[dict]) -> dict:
    t = _einzeilig(text).casefold()
    for k in kategorien:
        if t in (k["id"], k["name"].casefold()):
            return k
    raise ValueError(f"unbekannte Kategorie {t[:40]!r}")


BLOOM_TYPEN = ("erinnern", "verstehen", "anwenden", "analysieren", "bewerten", "erschaffen")
# übersetzte Typen (bei anderer Ausgabesprache kamen z. B. „appliquer“, „analizar“, „применять“ vor)
_BLOOM_ALIAS = {
    "remember": "erinnern", "recall": "erinnern", "se souvenir": "erinnern", "mémoriser": "erinnern",
    "recordar": "erinnern", "ricordare": "erinnern", "onthouden": "erinnern", "запоминать": "erinnern",
    "понимать": "verstehen", "understand": "verstehen", "comprendre": "verstehen", "comprender": "verstehen",
    "comprendere": "verstehen", "begrijpen": "verstehen", "anlamak": "verstehen",
    "apply": "anwenden", "appliquer": "anwenden", "aplicar": "anwenden", "applicare": "anwenden",
    "toepassen": "anwenden", "применять": "anwenden", "uygulamak": "anwenden",
    "analyze": "analysieren", "analyse": "analysieren", "analyser": "analysieren", "analizar": "analysieren",
    "analizzare": "analysieren", "analyseren": "analysieren", "анализировать": "analysieren",
    "evaluate": "bewerten", "évaluer": "bewerten", "evaluer": "bewerten", "evaluar": "bewerten",
    "valutare": "bewerten", "evalueren": "bewerten", "оценивать": "bewerten", "değerlendirmek": "bewerten",
    "create": "erschaffen", "créer": "erschaffen", "creer": "erschaffen", "crear": "erschaffen",
    "creare": "erschaffen", "creëren": "erschaffen", "создавать": "erschaffen", "erstellen": "erschaffen",
}


def bloom_typ(typ: str) -> str | None:
    """Bloom-Stufe als einer der sechs deutschen Werte; unbekannt = None."""
    t = typ.strip().casefold()
    return t if t in BLOOM_TYPEN else _BLOOM_ALIAS.get(t)


def parse_paare(data: dict | list, minimum: int) -> list[dict]:
    """Frage-Antwort-Paare; der Typ wird auf die sechs deutschen Bloom-Stufen gebracht, Paare mit unbekanntem Typ
    fallen weg (zählen also nicht zum Minimum)."""
    paare = data.get("paare", data.get("frage_antwort_paare", [])) if isinstance(data, dict) else data
    qa = []
    for p in paare:
        if not (isinstance(p, dict) and p.get("frage") and p.get("antwort")):
            continue
        typ = bloom_typ(str(p.get("typ") or ""))
        if typ:
            qa.append({"frage": str(p["frage"]).strip(), "antwort": str(p["antwort"]).strip(), "typ": typ})
    if len(qa) < minimum:
        raise ValueError(f"{len(qa)} Frage-Antwort-Paare")
    return qa


# --- Ein Material -----------------------------------------------------------------------------

def material_user(prompts: dict, values: dict, felder: dict, text: str, auftrag: str,
                  inhaltstyp: bool = True) -> str:
    v = {k: values.get(k) or LEER for k in ("bildungsstufe", "fach", "zielgruppe", "sprache")}
    v["inhaltstyp"] = (values.get("inhaltstyp") or LEER) if inhaltstyp else LEER
    v["titel"] = felder.get("titel") or LEER
    v["beschreibung"] = felder.get("beschreibung") or LEER
    v["keywords"] = ", ".join(felder.get("keywords") or []) or LEER
    v.update(volltext=text, auftrag=auftrag)
    return render(prompts["material"], v)


def parse_volltext(text: str, umfang_von: int | None = None) -> str:
    """Volltext; zu kurz ist er unter MIN_WOERTER oder unter 60 % der unteren Umfangsgrenze des Inhaltstyps."""
    t = text.strip()
    minimum = max(MIN_WOERTER, int(MIN_ANTEIL_UMFANG * umfang_von)) if umfang_von else 150
    if len(t.split()) < minimum:
        raise ValueError(f"Volltext mit {len(t.split())} Wörtern zu kurz")
    return t


@dataclass
class Anfrage:
    feld: str
    system: str
    user: str
    json_mode: bool = False


class Material:
    """Stand eines Materials: welche Felder fertig sind und welche Anfragen als Nächstes anstehen.

    anfragen() liefert die offenen Anfragen der aktuellen Stufe, antwort() prüft eine Antwort: Brauchbares wird
    übernommen, sonst wird das Feld neu angefragt, nach FELD_VERSUCHE Fehlversuchen ValueError. Der Stand lässt
    sich mit zustand() speichern und mit Material(..., zustand=...) fortsetzen (Batch-Verarbeitung)."""

    def __init__(self, seed: dict, prompts_by_bereich: dict, qa_min: int = 8, inhaltstyp_pruefen: bool = True,
                 ausgabesprache: str = "de", zustand: dict | None = None, bereich=None):
        self.seed = seed
        self.bereich = bereich or get_bereich(seed[S.F_BEREICH])  # bereich: schon geladen (viele Materialien)
        self.prompts = prompts_by_bereich[self.bereich.key]
        self.qa_min = qa_min
        self.ausgabesprache = ausgabesprache
        self.values = self.bereich.prompt_values(seed)
        # Umfang aus dem Seed (ältere Seeds: aus dem Inhaltstyp), Stilvorgaben nur, wenn der Seed welche hat
        umfang = seed.get(S.F_UMFANG) or dict(zip(("von", "bis"), self.bereich.umfang(seed)))
        self.values.setdefault("umfang_von", umfang["von"])
        self.values.setdefault("umfang_bis", umfang["bis"])
        stil = seed.get(S.F_STIL) or {}
        self.values.setdefault("einstieg", stil.get("einstieg", ""))
        self.values.setdefault("namen", ", ".join(stil.get("namen", [])))
        self.values.setdefault("beschreibung_form", satzform(str(seed.get("id", ""))))
        self.hinweis = ""
        if ausgabesprache != "de":
            name = SPRACHEN.get(ausgabesprache, ausgabesprache)
            self.hinweis = "\n\n" + render(self.prompts["ausgabesprache"], {"ausgabesprache": name})
            self.values["ausgabesprache"] = name  # für {{#ausgabesprache}}-Abschnitte (Beispiele sind deutsch)
            self.values["sprache"] = ", ".join(dict.fromkeys(
                [name, *[x for x in self.values.get("sprache", "").split(", ") if x and x != "Deutsch"]]))
        self.kategorien = self.bereich.inhaltstyp_kategorien() if inhaltstyp_pruefen else []
        z = zustand or {}
        self.felder: dict = dict(z.get("felder", {}))
        self.neu: dict[str, int] = dict(z.get("neu", {}))  # Fehlversuche je Feld
        self.tokens: dict[str, int] = dict(z.get("tokens", dict.fromkeys(TOKEN_FELDER, 0)))

    def zustand(self) -> dict:
        return {"felder": self.felder, "neu": self.neu, "tokens": self.tokens}

    @property
    def stufe(self) -> list[str] | None:
        """Felder der ersten Stufe, die noch nicht fertig ist; None = Material fertig."""
        for stufe in STUFEN:
            offen = [f for f in stufe if f not in self.felder and not (f == "inhaltstyp" and not self.kategorien)]
            if offen:
                return offen
        return None

    @property
    def fertig(self) -> bool:
        return self.stufe is None

    def anfragen(self) -> list[Anfrage]:
        return [self._anfrage(f) for f in self.stufe or []]

    def _anfrage(self, feld: str) -> Anfrage:
        p, v = self.prompts, self.values
        if feld == "volltext":
            return Anfrage(feld, render(p["volltext_system"], v) + self.hinweis, render(p["volltext"], v))
        system = render(p["system"], v) + self.hinweis
        text = self.felder["volltext"]
        if feld == "inhaltstyp":
            auftrag = render(p["inhaltstyp"], {
                "kategorien": "\n".join(f"- {k['name']} → {k['id']}" + (f": {k['definition']}" if k.get("definition") else "")
                                         for k in self.kategorien),
                "beispiel_id": self.kategorien[0]["id"]})
            # ohne den Inhaltstyp des Seeds: das Modell bestimmt ihn aus dem fertigen Material
            return Anfrage(feld, system, material_user(p, v, self.felder, text, auftrag, inhaltstyp=False))
        return Anfrage(feld, system, material_user(p, v, self.felder, text, render(p[feld], v)),
                       json_mode=feld == "fragen_antworten")

    def antwort(self, feld: str, text: str, tokens: dict | None = None) -> bool:
        """Prüft die Antwort auf ein Feld. True = übernommen, False = wird neu angefragt."""
        for k, n in (tokens or {}).items():
            self.tokens[k] = self.tokens.get(k, 0) + n
        try:
            self.felder[feld] = self._parse(feld, text)
            return True
        except ValueError as e:  # auch json.JSONDecodeError
            self.neu[feld] = self.neu.get(feld, 0) + 1
            if self.neu[feld] >= FELD_VERSUCHE:
                if feld == "volltext":
                    raise ValueError(f"Volltext nach {FELD_VERSUCHE} Versuchen zu kurz") from e
                raise ValueError(f"{feld} nach {FELD_VERSUCHE} Versuchen unbrauchbar: {e}") from e
            return False

    def _parse(self, feld: str, text: str):
        if feld == "volltext":
            return parse_volltext(text, self.values.get("umfang_von"))
        if feld == "inhaltstyp":
            k = parse_inhaltstyp(text, self.kategorien)
            return {"uri": k["uri"], "name": k["name"]}
        if feld == "fragen_antworten":
            return parse_paare(json_aus_text(text), self.qa_min)
        if feld == "titel":
            t = parse_titel(text)
            # weiche Prüfung: beim letzten Versuch wird der Titel trotzdem übernommen (das Wort kann zum Inhalt
            # gehören, etwa „Lied“ in einem Material über Lieder)
            if (w := titel_nennt_form(t, self.values)) and self.neu.get("titel", 0) < FELD_VERSUCHE - 1:
                raise ValueError(f"Titel nennt Materialtyp oder Stufe ({w!r})")
            return t
        if feld == "beschreibung":
            t = parse_beschreibung(text)
            # weiche Prüfung wie beim Titel: beim letzten Versuch wird die Beschreibung trotzdem übernommen
            if (f := beschreibung_floskel(t)) and self.neu.get("beschreibung", 0) < FELD_VERSUCHE - 1:
                raise ValueError(f"Beschreibung mit Schablone {f!r}")
            return t
        return {"titel": parse_titel, "keywords": parse_keywords}[feld](text)

    def ergebnis(self, meta: dict, dauer: float, mit_tokens: bool = True) -> dict:
        f, a = self.felder, self.ausgabesprache
        rec = dict(self.seed)
        rec[S.F_TITLE] = f["titel"]
        rec[S.F_DESCRIPTION] = f["beschreibung"]
        rec[S.F_KEYWORD] = f["keywords"]
        rec[S.F_FULLTEXT] = f["volltext"]
        rec[S.F_QA] = f["fragen_antworten"]
        if a != "de":
            rec[S.F_LANGUAGE] = list(dict.fromkeys([a, *[x for x in rec.get(S.F_LANGUAGE, []) if x != "de"]]))
        if "inhaltstyp" in f:
            erwartet = self.seed.get(S.F_LRT, [None])[0]
            rec[S.F_LRT_CHECK] = {"erwartet": erwartet, "erkannt": f["inhaltstyp"]["uri"],
                                  "erkannt_label": f["inhaltstyp"]["name"],
                                  "uebereinstimmung": f["inhaltstyp"]["uri"] == erwartet}
        prov = rec.setdefault(S.F_PROVENANCE, {})
        if a != "de":
            prov[S.F_LANGUAGE] = S.provenance("Generierung", method=f"Ausgabesprache {a} beim Erzeugen gewählt")
        prov[S.F_FULLTEXT] = dict(S.provenance("LLM", method="aus dem Seed erzeugt (volltext)"), modell=meta["modell"])
        for feld, datei in ((S.F_TITLE, "titel"), (S.F_DESCRIPTION, "beschreibung"), (S.F_KEYWORD, "keywords"),
                            (S.F_QA, "fragen_antworten")):
            prov[feld] = dict(S.provenance("LLM", method=f"aus Volltext und festen Metadaten erzeugt ({datei})"),
                              modell=meta["modell"])
        rec[S.F_GENERATION] = {**meta, "dauer_sekunden": round(dauer, 1), "neu_angefragt": self.neu}
        if mit_tokens and self.tokens.get("aufrufe"):
            rec[S.F_GENERATION]["tokens"] = self.tokens
        return rec


def generate_one(client: LLMClient, seed: dict, prompts_by_bereich: dict, meta: dict, qa_min: int = 8,
                 inhaltstyp_pruefen: bool = True, ausgabesprache: str = "de") -> dict:
    """Ein Material sofort erzeugen: Stufe für Stufe, die Felder einer Stufe gleichzeitig."""
    m = Material(seed, prompts_by_bereich, qa_min, inhaltstyp_pruefen, ausgabesprache)
    start = time.time()
    zaehlt = hasattr(client, "zaehler")

    def frage(a: Anfrage) -> tuple[str, dict]:
        if zaehlt:
            client.zaehler(neu=True)  # Zähler je Thread
        text = client.chat(a.system, a.user, json_mode=a.json_mode)
        return text, (dict(client.zaehler()) if zaehlt else {})

    while not m.fertig:
        anfragen = m.anfragen()
        if len(anfragen) == 1:
            ergebnisse = [frage(anfragen[0])]
        else:
            with ThreadPoolExecutor(len(anfragen)) as pool:
                ergebnisse = list(pool.map(frage, anfragen))
        for a, (text, tokens) in zip(anfragen, ergebnisse):
            m.antwort(a.feld, text, tokens)
    return m.ergebnis(meta, time.time() - start, mit_tokens=zaehlt)


def meta_fuer(cfg: LLMConfig, prompts_by_bereich: dict, inhaltstyp_pruefen: bool, ausgabesprache: str,
              verarbeitung: str | None = None) -> dict:
    """Angaben zur Erzeugung, die in jedem Material unter synth:generierung stehen."""
    pub = cfg.public()
    meta = {"modell": cfg.model, "base_url": pub["base_url"], "datum": date.today().isoformat(),
            "prompt_hash": {b: prompt_hash(p) for b, p in prompts_by_bereich.items()},
            "inhaltstyp_pruefung": inhaltstyp_pruefen, "ausgabesprache": ausgabesprache}
    if cfg.anbieter != "openai":
        meta["anbieter"] = cfg.anbieter  # fehlt = OpenAI-kompatibel
    if cfg.reasoning_effort:
        meta["reasoning_effort"] = cfg.reasoning_effort
    if verarbeitung or cfg.service_tier:
        meta["verarbeitung"] = verarbeitung or cfg.service_tier  # "flex" oder "batch"; fehlt = Standard
    return meta


def generate(seeds_path: Path, out_path: Path, cfg: LLMConfig, limit: int | None = None,
             inhaltstyp_pruefen: bool = True, ausgabesprache: str = "de", on_progress=print_progress,
             stop_event=None) -> Progress:
    from .laeufe import lauf_speichern

    lauf_speichern(out_path, seeds_path, cfg, limit, inhaltstyp_pruefen, ausgabesprache, cfg.service_tier or "sofort")
    seeds = read_seeds(seeds_path)
    if limit:
        seeds = seeds[:limit]
    bereiche = {s[S.F_BEREICH] for s in seeds}
    prompts_by_bereich = {b: load_prompts(b) for b in bereiche}
    meta = meta_fuer(cfg, prompts_by_bereich, inhaltstyp_pruefen, ausgabesprache)
    return run_tasks(seeds, lambda client, seed: generate_one(client, seed, prompts_by_bereich, meta,
                                                              inhaltstyp_pruefen=inhaltstyp_pruefen,
                                                              ausgabesprache=ausgabesprache),
                     Path(out_path), cfg, on_progress=on_progress, stop_event=stop_event)
