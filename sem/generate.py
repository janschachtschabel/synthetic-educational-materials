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
import re
import time
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
MIN_WOERTER = 150  # Volltext
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


def parse_titel(text: str) -> str:
    t = _einzeilig(_einzeilig(text.strip().splitlines()[0] if text.strip() else "").rstrip("."))
    if not 10 <= len(t) <= 120:
        raise ValueError(f"Titel mit {len(t)} Zeichen")
    return t


def parse_beschreibung(text: str) -> str:
    t = _einzeilig(text)
    if not 250 <= len(t) <= 1200:
        raise ValueError(f"Beschreibung mit {len(t)} Zeichen")
    return t


def parse_keywords(text: str, minimum: int = 4, maximum: int = 8) -> list[str]:
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


def parse_paare(data: dict | list, minimum: int) -> list[dict]:
    paare = data.get("paare", data.get("frage_antwort_paare", [])) if isinstance(data, dict) else data
    qa = [{k: str(p[k]).strip() for k in ("frage", "antwort", "typ") if p.get(k)}
          for p in paare if isinstance(p, dict) and p.get("frage") and p.get("antwort")]
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


def parse_volltext(text: str) -> str:
    t = text.strip()
    if len(t.split()) < MIN_WOERTER:
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
                "kategorien": "\n".join(f"- {k['name']} → {k['id']}" for k in self.kategorien),
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
            return parse_volltext(text)
        if feld == "inhaltstyp":
            k = parse_inhaltstyp(text, self.kategorien)
            return {"uri": k["uri"], "name": k["name"]}
        if feld == "fragen_antworten":
            return parse_paare(json_aus_text(text), self.qa_min)
        return {"titel": parse_titel, "beschreibung": parse_beschreibung, "keywords": parse_keywords}[feld](text)

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
