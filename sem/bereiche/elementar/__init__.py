"""Bildungsbereich Elementarbereich (frühe Bildung in Kindertageseinrichtungen).

Themen: GND-Sachbegriffe aus LLM-Themenlisten je Bildungsbereich und aus WLO-Keywords des
Elementarbereichs, per Mehrfachabstimmung als Thema für Angebote mit Kindern oder für Fachkräfte und Eltern
eingestuft und einem bis zwei der sieben Bildungsbereiche des Gemeinsamen Rahmens der Länder (Fassung 2022)
sowie einer Altersgruppe zugeordnet. Fach im OEH-Sinn: closeMatch der Bildungsbereiche auf OEH-Schulfächer.
"""

from __future__ import annotations

import json
import random
from functools import cache, cached_property

from ... import schema as S
from ...paths import MAPPING_DIR
from ...vocabs import load as load_vocab
from ..base import Bildungsbereich, MappingSchritt, SeedState
from ..hochschule import kurz_id, load_jsonl

DIR = MAPPING_DIR / "elementar"
CONTEXT_URI = "http://w3id.org/openeduhub/vocabs/educationalContext/elementarbereich"
F_BILDUNGSBEREICH = "synth:bildungsbereich_elementar"

__all__ = ["Elementar", "DIR", "bildungsbereiche", "load_jsonl"]


@cache
def bildungsbereiche() -> dict:
    return json.loads((DIR / "bildungsbereiche.json").read_text(encoding="utf-8"))


class Elementar(Bildungsbereich):
    key = "elementar"
    label = "Elementarbereich"
    balance_options = {
        "thema": "jedes Thema etwa gleich oft",
        "bildungsbereich": "jeder der sieben Bildungsbereiche etwa gleich oft",
        "altersgruppe": "unter 3, ab 3 und beide Altersgruppen etwa gleich oft",
        "art": "Themen für Angebote mit Kindern und Themen für Fachkräfte/Eltern etwa gleich oft",
    }
    default_balance = "bildungsbereich"
    mapping_folgen = {
        "themen": ["kandidaten_llm", "wlo", "gnd", "eignung", "bereiche", "alter"],
    }
    mapping_abschluss = ["zusammenfuehren"]

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import build as b

        S_ = MappingSchritt
        return [
            S_("kandidaten_llm", "Themenkandidaten", "typische Kita-Themen je Bildungsbereich aus drei Perspektiven",
               b.build_kandidaten_llm),
            S_("wlo", "WLO-Keywords", "Keywords der WLO-Datensätze im Elementarbereich (--wlo)", b.build_wlo, llm=False),
            S_("gnd", "GND-Abgleich", "Kandidaten exakt auf GND-Sachbegriffe (lobid)", b.build_gnd, llm=False),
            S_("eignung", "Eignung abstimmen", "Thema für Kinder, für Fachkräfte/Eltern oder ungeeignet (drei Stimmen)",
               b.build_eignung),
            S_("bereiche", "Bildungsbereiche abstimmen", "ein bis zwei Bildungsbereiche je Thema (drei Stimmen)",
               b.build_bereiche),
            S_("alter", "Altersgruppe abstimmen", "unter 3, ab 3 oder beide (drei Stimmen)", b.build_alter),
            S_("zusammenfuehren", "Zusammenführen", "themen_elementar.jsonl, Prüfliste, Korrekturen (läuft zuletzt)",
               b.zusammenfuehren, llm=False),
        ]

    def mapping_statistik(self) -> dict:
        from . import build

        return build.statistik() if (DIR / "themen_elementar.jsonl").exists() else {}

    # --- Auswahl der Themen ------------------------------------------------------------------

    def auswahl_optionen(self) -> dict[str, dict]:
        status = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                  "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                  "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
        std = ["gesichert", "wahrscheinlich", "redaktionell"]
        return {
            "art": {"label": "Themenart", "beschreibung": "Abstimmung über die Eignung",
                    "werte": {"kinderthema": "Inhalt für Angebote mit Kindern",
                              "fachthema": "Thema für Fachkräfte und Eltern"},
                    "standard": ["kinderthema", "fachthema"]},
            "herkunft": {"label": "Herkunft der Kandidaten", "beschreibung": "woher der Begriff als Kandidat kam",
                         "werte": {"llm": "LLM-Themenliste je Bildungsbereich", "wlo": "WLO-Keyword im Elementarbereich"},
                         "standard": ["llm", "wlo"]},
            "eignung_status": {"label": "Status der Eignung", "beschreibung": "Abstimmung", "werte": status,
                               "standard": std},
            "bereich_status": {"label": "Status des Bildungsbereichs", "beschreibung": "Abstimmung", "werte": status,
                               "standard": std},
            "alter_status": {"label": "Status der Altersgruppe", "beschreibung": "Abstimmung", "werte": status,
                             "standard": std},
            "belege": {"label": "Belege (mindestens einer)",
                       "beschreibung": "leer = keine Anforderung",
                       "werte": {"wlo": "Begriff als WLO-Keyword im Elementarbereich (mindestens zwei Datensätze)",
                                 "llm_bestaetigt": "Bildungsbereich der LLM-Kandidatenliste durch die Abstimmung bestätigt"},
                       "standard": []},
        }

    @staticmethod
    def belege_des_themas(topic: dict) -> set[str]:
        out = set()
        if (topic.get("belege") or {}).get("wlo"):
            out.add("wlo")
        if (topic.get("mapping") or {}).get("llm_kandidat_bestaetigt"):
            out.add("llm_bestaetigt")
        return out

    def passt(self, topic: dict, auswahl: dict[str, list[str]]) -> bool:
        m = topic.get("mapping") or {}
        if topic["art"] not in auswahl["art"] or not set(topic["herkunft"]) & set(auswahl["herkunft"]):
            return False
        if (m.get("eignung") or {}).get("status", "gesichert") not in auswahl["eignung_status"]:
            return False
        if m.get("status", "gesichert") not in auswahl["bereich_status"]:
            return False
        if (m.get("alter") or {}).get("status", "gesichert") not in auswahl["alter_status"]:
            return False
        return not auswahl["belege"] or bool(self.belege_des_themas(topic) & set(auswahl["belege"]))

    # --- Daten ------------------------------------------------------------------------------

    @cached_property
    def contexts(self):
        return load_vocab("educationalContext")

    @cached_property
    def roles(self):
        return load_vocab("intendedEndUserRole")

    @cached_property
    def disciplines(self):
        return load_vocab("discipline")

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
        path = DIR / "themen_elementar.jsonl"
        out = load_jsonl(path) if path.exists() else []
        if not out:
            raise FileNotFoundError(f"Keine Themen in {DIR}. Erst die Mappings bauen: python -m sem mappings elementar")
        return out

    def balance_key(self, topic: dict, balance: str) -> str:
        if balance == "thema":
            return topic["id"]
        if balance == "bildungsbereich":
            return topic["bildungsbereich"][0]["id"]
        return topic[balance]

    # --- Seed ---------------------------------------------------------------------------

    def make_seed(self, topic: dict, rng: random.Random, state: SeedState) -> dict:
        opt = self.optionen
        art = topic["art"]
        bereiche = {b["id"] for b in topic["bildungsbereich"]}
        role_w = opt["zielgruppe"]["werte"][art]
        role = rng.choices([r["uri"] for r in role_w], weights=[r["gewicht"] for r in role_w])[0]
        rolle = role.rsplit("/", 1)[-1]
        types = [t for t in self.inhaltstypen["inhaltstypen"] if art in t["arten"] and rolle in t["zielgruppen"]
                 and (not t["bereiche"] or bereiche & set(t["bereiche"]))]
        if not types:  # z. B. Eltern bei Fachthemen ohne passenden Typ
            types = [t for t in self.inhaltstypen["inhaltstypen"] if art in t["arten"]]
        typ_by_uri = {t["uri"]: t for t in types}
        typ = typ_by_uri[state.pick_least_used(f"typ:{state.group}:{rolle}", list(typ_by_uri), rng)]
        niveaus = [n for n in self.niveaus["niveaus"] if topic["altersgruppe"] in n["altersgruppen"]]
        niveau = rng.choice(niveaus or self.niveaus["niveaus"])

        seed: dict = {S.F_BEREICH: self.key}
        S.set_controlled(seed, S.F_CONTEXT, [self.contexts.ref(CONTEXT_URI)], S.provenance(
            "OpenEduHub educationalContext", uri=CONTEXT_URI, license="CC0-1.0", method="fest codiert"))
        bb = bildungsbereiche()
        alle = {b["id"]: b for b in bb["bildungsbereiche"]}
        fach_uris = list(dict.fromkeys(u for b in topic["bildungsbereich"] for u in alle[b["id"]]["closeMatch"][:2]))
        S.set_controlled(seed, S.F_TAXON, [self.disciplines.ref(u) for u in fach_uris], S.provenance(
            "OpenEduHub discipline", uri=self.disciplines.scheme["uri"], license="CC0-1.0",
            method="closeMatch des Bildungsbereichs (Handmapping des Projekts)"))
        m = topic.get("mapping", {})
        S.set_controlled(seed, F_BILDUNGSBEREICH, topic["bildungsbereich"], {
            **S.provenance(bb["quelle"]["name"], uri=bb["quelle"]["uri"], license=bb["quelle"]["lizenz"],
                           method=m.get("methode", "aus dem Thema übernommen")),
            **{k: m[k] for k in ("status", "konfidenz") if k in m}})
        lrt = self.inhaltstypen["quelle"]
        S.set_controlled(seed, S.F_LRT, [{"uri": typ["uri"], "label": typ["label"]}], S.provenance(
            lrt["name"], uri=lrt["uri"], license=lrt["lizenz"],
            method="passend zu Themenart, Zielgruppe und Bildungsbereich, je Gruppe rotierend"))
        S.set_controlled(seed, S.F_ROLE, [self.roles.ref(role)], S.provenance(
            "OpenEduHub intendedEndUserRole", uri=self.roles.scheme["uri"], license="CC0-1.0",
            method="gewichtet zufällig je Themenart"))
        seed[S.F_LANGUAGE] = opt["sprache"]["werte"]

        seed[S.F_THEMA] = {"id": topic["id"], "label_de": topic["label_de"],
                           "beschreibung_de": topic.get("definition", ""),
                           "keywords_de": list(dict.fromkeys([topic["label_de"], *topic.get("synonyme", [])[:4],
                                                              *[o["label"] for o in topic.get("oberbegriffe", [])][:2]])),
                           "art": art, "altersgruppe": topic["altersgruppe"]}
        q = topic["quelle"]
        seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(q["name"], uri=q["uri"], license=q["lizenz"],
                                                       method=m.get("methode", "Thema aus der Quelle"))
        seed[S.F_PROVENANCE][S.F_THEMA].update(
            status={"bildungsbereich": m.get("status"), "eignung": (m.get("eignung") or {}).get("status"),
                    "alter": (m.get("alter") or {}).get("status")},
            herkunft=topic["herkunft"], belege={"erfuellt": sorted(self.belege_des_themas(topic))})

        seed[S.F_NIVEAU] = {"id": niveau["id"], "label": niveau["label"], "alter": niveau["alter"],
                            "exactMatch": niveau["exactMatch"]}
        seed[S.F_PROVENANCE][S.F_NIVEAU] = S.provenance(self.niveaus["quelle"]["name"],
                                                        method="passend zur Altersgruppe des Themas, gleichverteilt")
        seed[S.F_MEDIENFORM] = typ["medienform"]
        seed["synth:textsorte"] = typ["textsorte"]
        if rng.random() < opt["didaktische_funktion"]["anteil"]:
            seed[S.F_DIDAKTIK] = rng.choice(opt["didaktische_funktion"]["werte"])
        if rng.random() < opt["bearbeitungsdauer_minuten"]["anteil"]:
            seed[S.F_LEARNING_TIME] = rng.choice(opt["bearbeitungsdauer_minuten"]["werte"])
        if rng.random() < opt["sozialform"]["anteil"]:
            seed["synth:sozialform"] = rng.choice(opt["sozialform"]["werte"])
        return seed

    # --- Prompt-Werte ----------------------------------------------------------------------

    def inhaltstyp_kategorien(self) -> list[dict]:
        return [{"id": kurz_id(t["label"]), "name": t["label"], "uri": t["uri"]}
                for t in self.inhaltstypen["inhaltstypen"]]

    def prompt_values(self, seed: dict) -> dict:
        t = seed[S.F_THEMA]
        rolle = seed[S.F_ROLE][0].rsplit("/", 1)[-1]
        return {
            "rolle": "eine erfahrene Erzieherin bzw. ein erfahrener Erzieher und Kindheitspädagogin bzw. Kindheitspädagoge",
            "sprache": ", ".join({"de": "Deutsch", "en": "Englisch"}.get(x, x) for x in seed.get(S.F_LANGUAGE, [])),
            "textsorte": seed["synth:textsorte"],
            "inhaltstyp": seed[S.F_LRT + "_DISPLAYNAME"][0],
            "medienform": seed[S.F_MEDIENFORM],
            "thema": t["label_de"],
            "thema_beschreibung": t.get("beschreibung_de", ""),
            "thema_keywords": ", ".join(t.get("keywords_de", [])),
            "themenart": {"kinderthema": "Inhalt für Angebote mit Kindern",
                          "fachthema": "pädagogisches Thema für Fachkräfte und Eltern"}[t["art"]],
            "fach": ", ".join(seed[F_BILDUNGSBEREICH + "_DISPLAYNAME"]),
            "bildungsstufe": seed[S.F_CONTEXT + "_DISPLAYNAME"][0],
            "niveau": seed[S.F_NIVEAU]["label"],
            "zielgruppe": {"teacher": "pädagogische Fachkräfte in der Kita", "parent": "Eltern",
                           "learner": "Kinder (zum Vorlesen oder gemeinsamen Singen durch Erwachsene)"}.get(
                rolle, seed[S.F_ROLE + "_DISPLAYNAME"][0]),
            "didaktische_funktion": seed.get(S.F_DIDAKTIK, ""),
            "bearbeitungsdauer": seed.get(S.F_LEARNING_TIME, ""),
            "sozialform": seed.get("synth:sozialform", ""),
        }
