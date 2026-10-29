"""Bildungsbereich Hochschule.

Themen: OpenAlex Topics (Forschungs- und Vertiefungsthemen) und Grundlagenthemen je Studienfach
(LLM-Vorschlag, über die GND belegt). Fächer: Destatis-Systematik in der Fassung der
OpenEduHub-Hochschulfächersystematik. Inhaltstypen: Auswahl aus OEH new_lrt.
"""

from __future__ import annotations

import json
import random
import re
from functools import cached_property

from ... import schema as S
from ...paths import MAPPING_DIR
from ...vocabs import load as load_vocab
from ..base import Bildungsbereich, MappingSchritt, SeedState

DIR = MAPPING_DIR / "hochschule"
THEMEN_FILES = ["themen_openalex.jsonl", "themen_grundlagen.jsonl"]
CONTEXT_URI = "http://w3id.org/openeduhub/vocabs/educationalContext/hochschule"

_PREFIX = re.compile(r"^Studienbereich\s+")
_ADMIN_NOTE = re.compile(r"\s*\((?:nicht |nur |ohne Lehramt|einschl\.).*?\)\s*$")


def clean_label(label: str) -> str:
    """Label für Prompts: ohne „Studienbereich“-Präfix und Verwaltungszusätze in Klammern."""
    return _ADMIN_NOTE.sub("", _PREFIX.sub("", label)).strip()


def load_jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class Hochschule(Bildungsbereich):
    key = "hochschule"
    label = "Hochschule"
    balance_options = {
        "thema": "jedes Thema etwa gleich oft",
        "studienfach": "jedes Destatis-Studienfach etwa gleich oft (Fächer mit wenigen Themen wiederholen diese)",
        "studienbereich": "jeder Destatis-Studienbereich etwa gleich oft",
        "faechergruppe": "jede Destatis-Fächergruppe etwa gleich oft",
        "lehrebene": "Grundlagen, Vertiefung und Forschung etwa gleich oft",
    }
    default_balance = "thema"
    mapping_folge = ["fachprofile", "uebersetzen", "kandidaten", "faecher", "lehrebene", "zusammenfuehren"]

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import build as b

        return [
            MappingSchritt("fachprofile", "Fachprofile", "Kurzbeschreibung und typische Themen je Studienfach", b.build_fachprofile),
            MappingSchritt("uebersetzen", "Übersetzen", "OpenAlex-Themen ins Deutsche (Vorhandenes wird übernommen)",
                           b.build_uebersetzen),
            MappingSchritt("kandidaten", "Kandidaten", "ähnlichste Studienfächer je Thema (lokale Embeddings)",
                           b.build_kandidaten, llm=False),
            MappingSchritt("faecher", "Fächer abstimmen", "drei Stimmen je Thema mit Begründung und Belegen", b.build_faecher),
            MappingSchritt("lehrebene", "Lehrebene abstimmen", "drei Stimmen je Thema nach festen Kriterien",
                           b.build_lehrebene),
            MappingSchritt("zusammenfuehren", "Zusammenführen", "Auswertung, Konfidenz, Prüfliste, Korrekturen",
                           b.zusammenfuehren, llm=False),
            MappingSchritt("grundlagen", "Grundlagenthemen", "je Studienfach per LLM, über die GND belegt",
                           b.build_grundlagen),
            MappingSchritt("gnd", "GND-Abgleich", "GND-Abgleich der Grundlagen-Vorschläge wiederholen", b.build_gnd,
                           llm=False),
        ]

    def mapping_statistik(self) -> dict:
        from . import build

        return build.statistik()

    @cached_property
    def faecher(self):
        return load_vocab("hochschulfaechersystematik")

    @cached_property
    def context(self):
        return load_vocab("educationalContext").ref(CONTEXT_URI)

    @cached_property
    def roles(self):
        return load_vocab("intendedEndUserRole")

    @cached_property
    def inhaltstypen(self):
        return json.loads((DIR / "inhaltstypen.json").read_text(encoding="utf-8"))

    @cached_property
    def niveaus(self):
        return json.loads((DIR / "niveaus.json").read_text(encoding="utf-8"))

    @cached_property
    def optionen(self):
        return json.loads((DIR / "optionale_felder.json").read_text(encoding="utf-8"))

    def topics(self) -> list[dict]:
        return self._topics

    @cached_property
    def _topics(self) -> list[dict]:
        out = []
        for name in THEMEN_FILES:
            if (DIR / name).exists():
                out += load_jsonl(DIR / name)
        if not out:
            raise FileNotFoundError(
                f"Keine Themen in {DIR}. Erst die Mappings bauen: python -m sem mappings hochschule")
        return out

    def balance_key(self, topic: dict, balance: str) -> str:
        if balance == "thema":
            return topic["id"]
        if balance == "lehrebene":
            return topic.get("lehrebene") or "unbekannt"
        return topic[balance][0]["uri"]

    # --- Seed ---------------------------------------------------------------------------

    def _fitting_types(self, topic: dict) -> list[dict]:
        ebene = topic.get("lehrebene") or "vertiefung"
        gruppen = {g["notation"] for g in topic["faechergruppe"]}
        types = [t for t in self.inhaltstypen["inhaltstypen"]
                 if ebene in t["lehrebenen"] and (not t["faechergruppen"] or gruppen & set(t["faechergruppen"]))]
        return types or self.inhaltstypen["inhaltstypen"]

    def make_seed(self, topic: dict, rng: random.Random, state: SeedState) -> dict:
        typ_by_uri = {t["uri"]: t for t in self.inhaltstypen["inhaltstypen"]}
        typ = typ_by_uri[state.pick_least_used(f"typ:{state.group}", [t["uri"] for t in self._fitting_types(topic)], rng)]
        ebene = topic.get("lehrebene")
        niveaus = [n for n in self.niveaus["niveaus"] if ebene not in n["ausgeschlossen_fuer_lehrebene"]]
        niveau = rng.choice(niveaus)
        opt = self.optionen

        seed: dict = {S.F_BEREICH: self.key}
        lrt_src = self.inhaltstypen["quelle"]
        vocab_hfs = self.faecher.scheme
        S.set_controlled(seed, S.F_CONTEXT, [self.context], S.provenance(
            "OpenEduHub educationalContext", uri=CONTEXT_URI, license="CC0-1.0", method="fest codiert"))
        m = topic.get("mapping", {})
        prov = S.provenance("OpenEduHub-Hochschulfächersystematik (Destatis)", uri=vocab_hfs["uri"],
                            license=vocab_hfs["license"], method=m.get("methode", "aus dem Thema übernommen"))
        prov.update({k: m[k] for k in ("status", "konfidenz") if k in m})
        S.set_controlled(seed, S.F_TAXON_UNI, topic["studienfach"], prov)
        S.set_controlled(seed, S.F_LRT, [{"uri": typ["uri"], "label": typ["label"]}], S.provenance(
            lrt_src["name"], uri=lrt_src["uri"], license=lrt_src["lizenz"],
            method="passend zur Lehrebene, je Gruppe rotierend (seltenster Typ zuerst)"))

        role_w = opt["zielgruppe"]["werte"]
        role = rng.choices([r["uri"] for r in role_w], weights=[r["gewicht"] for r in role_w])[0]
        S.set_controlled(seed, S.F_ROLE, [self.roles.ref(role)], S.provenance(
            "OpenEduHub intendedEndUserRole", uri=self.roles.scheme["uri"], license="CC0-1.0",
            method="gewichtet zufällig"))
        seed[S.F_LANGUAGE] = opt["sprache"]["werte"]

        seed[S.F_THEMA] = {k: topic[k] for k in ("id", "label_de", "label_en", "beschreibung_de", "keywords_de")
                           if topic.get(k)}
        seed[S.F_THEMA]["lehrebene"] = ebene
        seed[S.F_THEMA]["studienbereich"] = topic["studienbereich"]
        seed[S.F_THEMA]["faechergruppe"] = topic["faechergruppe"]
        q = topic["quelle"]
        seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(q["name"], uri=q["uri"], license=q["lizenz"], method=topic.get(
            "mapping", {}).get("methode", "Thema aus der Quelle"))

        seed[S.F_NIVEAU] = {k: niveau[k] for k in ("id", "label", "dqr", "exactMatch")}
        seed[S.F_PROVENANCE][S.F_NIVEAU] = S.provenance(
            self.niveaus["quelle"]["name"], method="gleichverteilt, ohne ausgeschlossene Kombinationen")
        seed[S.F_MEDIENFORM] = typ["medienform"]
        seed["synth:textsorte"] = typ["textsorte"]

        if rng.random() < opt["didaktische_funktion"]["anteil"]:
            seed[S.F_DIDAKTIK] = rng.choice(opt["didaktische_funktion"]["werte"])
        if rng.random() < opt["bearbeitungsdauer_minuten"]["anteil"]:
            seed[S.F_LEARNING_TIME] = rng.choice(opt["bearbeitungsdauer_minuten"]["werte"])
        return seed

    # --- Prompt-Werte ----------------------------------------------------------------------

    def prompt_values(self, seed: dict) -> dict:
        t = seed[S.F_THEMA]
        return {
            "textsorte": seed["synth:textsorte"],
            "inhaltstyp": seed[S.F_LRT + "_DISPLAYNAME"][0],
            "medienform": seed[S.F_MEDIENFORM],
            "thema": t.get("label_de") or t.get("label_en"),
            "thema_beschreibung": t.get("beschreibung_de", ""),
            "thema_keywords": ", ".join(t.get("keywords_de", [])),
            "fach": ", ".join(clean_label(l) for l in seed[S.F_TAXON_UNI + "_DISPLAYNAME"]),
            "bildungsstufe": seed[S.F_CONTEXT + "_DISPLAYNAME"][0],
            "niveau": seed[S.F_NIVEAU]["label"],
            "zielgruppe": {"learner": "Studierende", "teacher": "Lehrende"}.get(
                seed[S.F_ROLE][0].rsplit("/", 1)[-1], seed[S.F_ROLE + "_DISPLAYNAME"][0]),
            "didaktische_funktion": seed.get(S.F_DIDAKTIK, ""),
            "bearbeitungsdauer": seed.get(S.F_LEARNING_TIME, ""),
        }
