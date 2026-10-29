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
THEMEN_FILES = ["themen_openalex.jsonl", "themen_gnd.jsonl"]
CONTEXT_URI = "http://w3id.org/openeduhub/vocabs/educationalContext/hochschule"

_PREFIX = re.compile(r"^Studienbereich\s+")
_ADMIN_NOTE = re.compile(r"\s*\((?:nicht |nur |ohne Lehramt|einschl\.).*?\)\s*$")


def clean_label(label: str) -> str:
    """Label für Prompts: ohne „Studienbereich“-Präfix und Verwaltungszusätze in Klammern."""
    return _ADMIN_NOTE.sub("", _PREFIX.sub("", label)).strip()


def kurz_id(label: str) -> str:
    """Lesbare id für Prompts: „Lösungs(beispiel) und Erwartungshorizont“ → loesungsbeispiel_und_erwartungshorizont."""
    s = label.casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("(", ""), (")", "")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


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
    mapping_folgen = {
        "openalex": ["fachprofile", "uebersetzen", "kandidaten", "faecher", "lehrebene", "zusammenfuehren"],
        "wlo": ["wlo", "wlo_belege"],
        "gnd": ["gnd_sachgruppen", "bruecke", "gnd_pool", "gnd_relevanz", "gnd_kandidaten", "gnd_faecher",
                "gnd_lehrebene", "gnd_zusammenfuehren"],
    }

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import build as b
        from . import gnd_themen as g

        S_ = MappingSchritt
        return [
            S_("fachprofile", "Fachprofile", "Kurzbeschreibung und typische Themen je Studienfach", b.build_fachprofile),
            S_("uebersetzen", "Übersetzen", "OpenAlex-Themen ins Deutsche (Vorhandenes wird übernommen)", b.build_uebersetzen),
            S_("kandidaten", "Kandidaten", "ähnlichste Studienfächer je OpenAlex-Thema (lokale Embeddings)",
               b.build_kandidaten, llm=False),
            S_("faecher", "Fächer abstimmen", "drei Stimmen je OpenAlex-Thema mit Begründung und Belegen", b.build_faecher),
            S_("lehrebene", "Lehrebene abstimmen", "drei Stimmen je OpenAlex-Thema nach festen Kriterien", b.build_lehrebene),
            S_("zusammenfuehren", "Zusammenführen", "OpenAlex: Auswertung, Konfidenz, Prüfliste, Korrekturen",
               b.zusammenfuehren, llm=False),
            S_("wlo", "WLO-Keywords", "Keyword-Fach-Kombinationen aus WLO-Exporten (--wlo)", g.build_wlo, llm=False),
            S_("wlo_belege", "WLO-Belege", "WLO-Keywords exakt auf OpenAlex-Themen, Fachabgleich", g.build_wlo_belege,
               llm=False),
            S_("gnd_sachgruppen", "GND-Sachgruppen", "Vokabular der GND-Sachgruppen laden", g.build_gnd_sachgruppen,
               llm=False),
            S_("bruecke", "Brücke Fach → Sachgruppe", "Studienfach → GND-Sachgruppen (drei Stimmen)", g.build_bruecke),
            S_("gnd_pool", "GND-Pool", "Sachbegriffe aus Lehrbüchern, Hochschulschriften und WLO-Keywords (lobid)",
               g.build_gnd_pool, llm=False),
            S_("gnd_relevanz", "Hochschulrelevanz", "drei Stimmen: Lehrinhalt an Hochschulen?", g.build_gnd_relevanz),
            S_("gnd_kandidaten", "GND-Kandidaten", "ähnlichste Studienfächer je Begriff", g.build_gnd_kandidaten, llm=False),
            S_("gnd_faecher", "GND-Fächer", "drei Stimmen je Begriff", g.build_gnd_faecher),
            S_("gnd_lehrebene", "GND-Lehrebene", "drei Stimmen je Begriff", g.build_gnd_lehrebene),
            S_("gnd_zusammenfuehren", "GND zusammenführen", "themen_gnd.jsonl, Prüfliste, Ausschlüsse",
               g.build_gnd_zusammenfuehren, llm=False),
        ]

    # --- Auswahl der Themen ------------------------------------------------------------------

    def auswahl_optionen(self) -> dict[str, dict]:
        status = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                  "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                  "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
        return {
            "quellen": {"label": "Themenquellen", "beschreibung": "woher die Themen stammen",
                        "werte": {"openalex": "OpenAlex Topics (Forschungs- und Vertiefungsthemen)",
                                  "gnd": "GND-Sachschlagwörter aus Lehrbüchern, Hochschulschriften und WLO-Keywords"},
                        "standard": ["openalex", "gnd"]},
            "fach_status": {"label": "Status der Fachzuordnung", "beschreibung": "Abstimmung über das Studienfach",
                            "werte": status, "standard": ["gesichert", "wahrscheinlich", "redaktionell"]},
            "lehrebene_status": {"label": "Status der Lehrebene", "beschreibung": "Grundlagen, Vertiefung, Forschung",
                                 "werte": status, "standard": ["gesichert", "wahrscheinlich", "redaktionell"]},
            "hochschulrelevanz": {"label": "Hochschulrelevanz (nur GND)",
                                  "beschreibung": "Abstimmung, ob der Begriff an Hochschulen gelehrt wird; "
                                                  "OpenAlex-Themen gelten als hochschulrelevant",
                                  "werte": {k: status[k] for k in ("gesichert", "wahrscheinlich", "unsicher")},
                                  "standard": ["gesichert", "wahrscheinlich"]},
            "belege": {"label": "Belege (mindestens einer)",
                       "beschreibung": "leer = keine Anforderung; sonst muss das Thema mindestens einen der "
                                       "gewählten Belege haben",
                       "werte": {"lehrbuch": "Lehrbuch mit diesem Schlagwort im hbz-Katalog",
                                 "hochschulschrift": "mindestens 3 Hochschulschriften im hbz-Katalog",
                                 "wlo": "WLO-Keyword in Verbindung mit einem passenden Hochschulfach",
                                 "handmapping": "Fach durch Handmapping (OpenAlex-Teilgebiet) bzw. "
                                                "GND-Sachgruppen-Brücke bestätigt"},
                       "standard": []},
        }

    @staticmethod
    def belege_des_themas(topic: dict) -> set[str]:
        b = topic.get("belege") or {}
        out = set()
        kat = b.get("katalog") or {}
        if kat.get("lehrbuecher", 0) >= 1:
            out.add("lehrbuch")
        if kat.get("hochschulschriften", 0) >= 3:
            out.add("hochschulschrift")
        wlo = b.get("wlo")
        if wlo:
            if "fach_passt" in wlo:
                passt = wlo["fach_passt"] != "nein"
            else:
                eigene = {f["uri"] for k in ("studienfach", "studienbereich", "faechergruppe") for f in topic[k]}
                passt = bool(eigene & {f["uri"] for f in wlo.get("faecher", [])})
            if passt:
                out.add("wlo")
        if (topic.get("mapping") or {}).get("handmapping_bestaetigt"):
            out.add("handmapping")
        return out

    def passt(self, topic: dict, auswahl: dict[str, list[str]]) -> bool:
        m = topic.get("mapping") or {}
        if topic.get("art", "openalex") not in auswahl["quellen"]:
            return False
        if m.get("status", "gesichert") not in auswahl["fach_status"]:
            return False
        if (m.get("lehrebene") or {}).get("status", "gesichert") not in auswahl["lehrebene_status"]:
            return False
        rel = m.get("hochschulrelevanz")
        if rel and rel.get("status") not in auswahl["hochschulrelevanz"]:
            return False
        return not auswahl["belege"] or bool(self.belege_des_themas(topic) & set(auswahl["belege"]))

    def mapping_statistik(self) -> dict:
        from . import build, gnd_themen

        out = {"openalex": build.statistik()}
        if gnd_themen.THEMEN_GND.exists():
            out["gnd"] = build.statistik(load_jsonl(gnd_themen.THEMEN_GND))
        return out

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

    @staticmethod
    def status_des_themas(topic: dict) -> dict:
        m = topic.get("mapping") or {}
        out = {"fach": m.get("status"), "lehrebene": (m.get("lehrebene") or {}).get("status")}
        if m.get("hochschulrelevanz"):
            out["hochschulrelevanz"] = m["hochschulrelevanz"].get("status")
        return {k: v for k, v in out.items() if v}

    @classmethod
    def belege_kompakt(cls, topic: dict) -> dict:
        """Nachweise für den Seed: Katalogzahlen mit Abfrage-URI, WLO-Datensätze mit Node-Links."""
        from ...quellen.wlo import NODE_URI

        b = topic.get("belege") or {}
        out: dict = {"erfuellt": sorted(cls.belege_des_themas(topic))}
        if b.get("katalog"):
            k = b["katalog"]
            out["katalog"] = {x: k.get(x, 0) for x in ("titel", "lehrbuecher", "hochschulschriften")}
            out["katalog"]["quelle"] = k.get("quelle")
        if b.get("wlo"):
            w = b["wlo"]
            out["wlo"] = {"datensaetze": w.get("datensaetze"), "quelle": w.get("quelle"),
                          "nodes": [NODE_URI.format(n) for n in w.get("nodes", [])[:10]]}
            if "fach_passt" in w:
                out["wlo"]["fach_passt"] = w["fach_passt"]
        return out

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
        seed[S.F_PROVENANCE][S.F_THEMA].update(status=self.status_des_themas(topic), belege=self.belege_kompakt(topic))

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

    def inhaltstyp_kategorien(self) -> list[dict]:
        return [{"id": kurz_id(t["label"]), "name": t["label"], "uri": t["uri"]}
                for t in self.inhaltstypen["inhaltstypen"]]

    def prompt_values(self, seed: dict) -> dict:
        t = seed[S.F_THEMA]
        return {
            "rolle": "eine erfahrene Hochschullehrkraft",
            "sprache": ", ".join({"de": "Deutsch", "en": "Englisch"}.get(x, x) for x in seed.get(S.F_LANGUAGE, [])),
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
