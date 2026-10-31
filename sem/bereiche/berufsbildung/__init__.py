"""Bildungsbereich Berufliche Bildung.

Themen: Kombinationen aus ESCO-Beruf und einer Kompetenz, die an diesem Beruf hängt (essenziell, optional
wählbar), und Lernfelder der KMK-Rahmenlehrpläne im zugeordneten ESCO-Beruf (Themenquelle „lernfeld“). Berufe und Kompetenzen tragen die ESCO-Original-URIs; Kompetenzen zusätzlich den Abgleich mit dem
OEH-Vokabular escoSkills. Berufsfeld: KldB 2010 (Fassung 2020) aus dem OEH-Vokabular kldb, wie es WLO im
Feld ccm:oeh_profession_group nutzt. Qualifizierungsweg (Ausbildung, Fortbildung, Studium, Anlernen) und
KldB-Zuordnung je Beruf per Mehrfachabstimmung, filterbar über auswahl_optionen.
"""

from __future__ import annotations

import json
import random
from functools import cached_property

from ... import schema as S
from ...paths import MAPPING_DIR, lokal_pfad
from ...vocabs import load as load_vocab
from ..base import Bildungsbereich, MappingSchritt, SeedState
from ..hochschule import kurz_id, load_jsonl

DIR = MAPPING_DIR / "berufsbildung"
CONTEXT_BASE = "http://w3id.org/openeduhub/vocabs/educationalContext/"
KLDB_EBENEN = ["berufsuntergruppe", "berufsgruppe", "berufshauptgruppe", "berufsbereich"]

__all__ = ["Berufsbildung", "DIR", "load_jsonl"]


class Berufsbildung(Bildungsbereich):
    key = "berufsbildung"
    label = "Berufliche Bildung"
    balance_options = {
        "thema": "jede Beruf-Kompetenz-Kombination etwa gleich oft",
        "beruf": "jeder ESCO-Beruf etwa gleich oft (Kompetenzen rotieren)",
        "kompetenz": "jede Kompetenz etwa gleich oft (Berufe rotieren)",
        "berufsgruppe": "jede KldB-Berufsgruppe (3-Steller) etwa gleich oft",
        "berufshauptgruppe": "jede KldB-Berufshauptgruppe (2-Steller) etwa gleich oft",
        "berufsbereich": "jeder KldB-Berufsbereich (1-Steller) etwa gleich oft",
    }
    default_balance = "beruf"
    kombination_label = "Beruf × Kompetenz"
    mapping_folgen = {
        "esco": ["esco", "hinweise", "kandidaten", "kldb", "qualifizierung"],
        "wlo": ["wlo"],
        "lernfeld": ["lernfelder", "rlp_kandidaten", "rlp_berufe", "rlp_zusammenfuehren"],
    }
    mapping_abschluss = ["zusammenfuehren"]

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import build as b
        from . import lernfelder as lf

        S_ = MappingSchritt
        return [
            S_("esco", "ESCO laden", "ESCO-Berufe mit essenziellen und optionalen Kompetenzen (API, Zwischenspeicher)",
               b.build_esco, llm=False),
            S_("hinweise", "BA-Umsteigeschlüssel", "ISCO-08 → KldB-Berufsuntergruppen und Anforderungsniveaus",
               b.build_hinweise, llm=False),
            S_("kandidaten", "Kandidaten", "ähnlichste KldB-Berufsuntergruppen je Beruf (lokale Embeddings)",
               b.build_kandidaten, llm=False),
            S_("kldb", "KldB abstimmen", "drei Stimmen je Beruf mit Begründung und Belegen", b.build_kldb),
            S_("qualifizierung", "Qualifizierungsweg abstimmen", "Ausbildung, Fortbildung, Studium oder Anlernen "
               "(drei Stimmen)", b.build_qualifizierung),
            S_("wlo", "WLO-Keywords", "Keywords und Berufsgruppen aus WLO-Exporten (--wlo), nur berufliche Kontexte",
               b.build_wlo, llm=False),
            S_("lernfelder", "Lernfelder einlesen", "KMK-Rahmenlehrpläne (Markdown, --rlp ORDNER): Nummer, Titel, "
               "Jahr und Zeitrichtwert je Lernfeld", lf.build_lernfelder, llm=False),
            S_("rlp_kandidaten", "Kandidaten Rahmenlehrplan", "ähnlichste ESCO-Berufe je Rahmenlehrplan "
               "(lokale Embeddings)", lf.build_rlp_kandidaten, llm=False),
            S_("rlp_berufe", "ESCO-Berufe abstimmen", "welche ESCO-Berufe dem Ausbildungsberuf entsprechen "
               "(drei Stimmen)", lf.build_rlp_berufe),
            S_("rlp_zusammenfuehren", "Rahmenlehrpläne zusammenführen", "rahmenlehrplaene.jsonl, Prüfliste, "
               "Korrekturen", lf.zusammenfuehren, llm=False),
            S_("zusammenfuehren", "Zusammenführen", "berufe.jsonl, kompetenzen.jsonl, Prüfliste, Korrekturen "
               "(läuft zuletzt)", b.zusammenfuehren, llm=False),
        ]

    def mapping_statistik(self) -> dict:
        from . import build, lernfelder

        out = build.statistik() if (DIR / "berufe.jsonl").exists() else {}
        return {**out, "lernfelder": lernfelder.statistik()} if lokal_pfad(lernfelder.PLAENE).exists() else out

    # --- Auswahl der Themen ------------------------------------------------------------------

    def auswahl_optionen(self) -> dict[str, dict]:
        status = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                  "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                  "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
        return {
            "quellen": {"label": "Themenquellen",
                        "beschreibung": "woraus die Themen stammen",
                        "werte": {"esco": "ESCO-Beruf × Kompetenz des Berufs",
                                  "lernfeld": "ESCO-Beruf × Lernfeld des KMK-Rahmenlehrplans"},
                        "standard": ["esco", "lernfeld"]},
            "qualifizierung": {"label": "Qualifizierungsweg des Berufs",
                               "beschreibung": "wie der Beruf in Deutschland üblicherweise erlernt wird (Abstimmung)",
                               "werte": {"ausbildung": "Berufsausbildung (dual oder schulisch)",
                                         "fortbildung": "Aufstiegsfortbildung nach einer Ausbildung",
                                         "studium": "üblicherweise Hochschulstudium",
                                         "anlernen": "Helfer- und Anlerntätigkeit"},
                               "standard": ["ausbildung", "fortbildung"]},
            "relation": {"label": "Bindung der Kompetenz an den Beruf", "beschreibung": "laut ESCO",
                         "werte": {"essenziell": "essenzielle Kompetenz des Berufs",
                                   "optional": "optionale Kompetenz des Berufs"},
                         "standard": ["essenziell"]},
            "kompetenzart": {"label": "Art der Kompetenz", "beschreibung": "ESCO-Kompetenzart",
                             "werte": {"wissen": "Kenntnisse", "faehigkeit": "Fähigkeiten und Kompetenzen"},
                             "standard": ["wissen", "faehigkeit"]},
            "reichweite": {"label": "Reichweite der Kompetenz", "beschreibung": "ESCO-Wiederverwendbarkeit",
                           "werte": {"berufsspezifisch": "nur in diesem Beruf",
                                     "branchenspezifisch": "in einer Branche",
                                     "branchenuebergreifend": "in mehreren Branchen",
                                     "querschnitt": "Querschnittskompetenz (z. B. Teamarbeit)"},
                           "standard": ["berufsspezifisch", "branchenspezifisch", "branchenuebergreifend"]},
            "kldb_status": {"label": "Status der KldB-Zuordnung", "beschreibung": "Abstimmung über die Berufsgruppe",
                            "werte": status, "standard": ["gesichert", "wahrscheinlich", "redaktionell"]},
            "qualifizierung_status": {"label": "Status des Qualifizierungswegs", "beschreibung": "Abstimmung",
                                      "werte": status, "standard": ["gesichert", "wahrscheinlich", "redaktionell"]},
            "lernfeld_status": {"label": "Status der Zuordnung Rahmenlehrplan → ESCO-Beruf",
                                "beschreibung": "Abstimmung (nur Lernfeld-Themen)", "werte": status,
                                "standard": ["gesichert", "wahrscheinlich", "redaktionell"]},
            "belege": {"label": "Belege (mindestens einer)",
                       "beschreibung": "leer = keine Anforderung; sonst muss das Thema mindestens einen der "
                                       "gewählten Belege haben",
                       "werte": {"wlo": "Kompetenz als WLO-Keyword in beruflichen Kontexten mit passender Berufsgruppe",
                                 "wlo_beruf": "Beruf als WLO-Keyword in beruflichen Kontexten",
                                 "handmapping": "KldB-Zuordnung durch den BA-Umsteigeschlüssel ISCO-08 → KldB bestätigt"},
                       "standard": []},
        }

    @staticmethod
    def wlo_fach_passt(kompetenz: dict, beruf: dict) -> bool:
        """WLO-Datensätze mit diesem Keyword tragen eine Berufsgruppe aus derselben KldB-Berufsgruppe (3-Steller)."""
        w = (kompetenz.get("belege") or {}).get("wlo")
        if not w:
            return False
        eigene = {x["notation"][:3] for x in beruf["berufsuntergruppe"]}
        return any(k[:3] in eigene for k in w.get("kldb", {}) if len(k) >= 3)

    def belege_des_themas(self, topic: dict) -> set[str]:
        out = set()
        b, k = topic["beruf"], topic.get("kompetenz")
        if k and self.wlo_fach_passt(k, b):
            out.add("wlo")
        if (b.get("belege") or {}).get("wlo"):
            out.add("wlo_beruf")
        if (b.get("mapping") or {}).get("handmapping_bestaetigt"):
            out.add("handmapping")
        return out

    def passt(self, topic: dict, auswahl: dict[str, list[str]]) -> bool:
        b, k = topic["beruf"], topic.get("kompetenz")
        m = b.get("mapping") or {}
        if topic.get("quelle", "esco") not in auswahl["quellen"] or b.get("qualifizierung") not in auswahl["qualifizierung"]:
            return False
        if k is None:  # Lernfeld-Thema: die Kompetenzfilter gelten nicht
            if topic["plan"]["mapping"]["status"] not in auswahl["lernfeld_status"]:
                return False
        elif topic["relation"] not in auswahl["relation"] or k.get("art") not in auswahl["kompetenzart"] \
                or k.get("reichweite") not in auswahl["reichweite"]:
            return False
        if m.get("status", "gesichert") not in auswahl["kldb_status"]:
            return False
        if (m.get("qualifizierung") or {}).get("status", "gesichert") not in auswahl["qualifizierung_status"]:
            return False
        return not auswahl["belege"] or bool(self.belege_des_themas(topic) & set(auswahl["belege"]))

    # --- Daten ------------------------------------------------------------------------------

    @cached_property
    def kldb(self):
        return load_vocab("kldb")

    @cached_property
    def contexts(self):
        return load_vocab("educationalContext")

    @cached_property
    def roles(self):
        return load_vocab("intendedEndUserRole")

    @cached_property
    def dqr(self):
        return load_vocab("dqrCompetenceLevels")

    @cached_property
    def inhaltstypen(self):
        return json.loads((DIR / "inhaltstypen.json").read_text(encoding="utf-8"))

    @cached_property
    def niveaus(self):
        return json.loads((DIR / "niveaus.json").read_text(encoding="utf-8"))

    @cached_property
    def optionen(self):
        return json.loads((DIR / "optionale_felder.json").read_text(encoding="utf-8"))

    @cached_property
    def berufe(self) -> list[dict]:
        path = DIR / "berufe.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"Keine Berufe in {DIR}. Erst die Mappings bauen: python -m sem mappings berufsbildung")
        return load_jsonl(path)

    @cached_property
    def kompetenzen(self) -> dict[str, dict]:
        return {k["uri"]: k for k in load_jsonl(DIR / "kompetenzen.jsonl")}

    def topics(self) -> list[dict]:
        return self._topics

    @cached_property
    def _topics(self) -> list[dict]:
        out = []
        for b in self.berufe:
            for relation in ("essenziell", "optional"):
                for uri in b[relation]:
                    k = self.kompetenzen.get(uri)
                    if k:
                        out.append({"id": f"{b['uri']}|{uri.rsplit('/', 1)[-1]}", "beruf": b, "kompetenz": k,
                                    "relation": relation})
        return out + self._lernfeld_topics()

    def _lernfeld_topics(self) -> list[dict]:
        from .lernfelder import lade_plaene

        by_uri = {b["uri"]: b for b in self.berufe}
        out = []
        for p in lade_plaene():
            for eb in p["berufe"]:
                b = by_uri.get(eb["uri"])
                for lf in p["lernfelder"] if b else []:
                    out.append({"id": f"{b['uri']}|{p['id']}:{lf['nr']}", "beruf": b, "lernfeld": lf, "plan": p,
                                "quelle": "lernfeld"})
        return out

    def balance_key(self, topic: dict, balance: str) -> str:
        if balance == "thema":
            return topic["id"]
        if balance == "beruf":
            return topic["beruf"]["uri"]
        if balance == "kompetenz":
            return topic["kompetenz"]["uri"] if "kompetenz" in topic else topic["id"]
        return topic["beruf"][balance][0]["uri"]

    # --- Seed ---------------------------------------------------------------------------

    @staticmethod
    def _weg(quali: str) -> str:
        """Qualifizierungsweg → Gruppe für Lernorte (ausbildung/fortbildung)."""
        return "fortbildung" if quali in ("fortbildung", "studium") else "ausbildung"

    def make_seed(self, topic: dict, rng: random.Random, state: SeedState) -> dict:
        b, k = topic["beruf"], topic.get("kompetenz") or {}
        lf = topic.get("lernfeld")
        opt = self.optionen
        quali = b.get("qualifizierung") or "ausbildung"

        role_w = opt["zielgruppe"]["werte"]
        role = rng.choices([r["uri"] for r in role_w], weights=[r["gewicht"] for r in role_w])[0]
        rolle_kurz = role.rsplit("/", 1)[-1]
        types = [t for t in self.inhaltstypen["inhaltstypen"]
                 if k.get("art") in t["kompetenzarten"] and rolle_kurz in t["zielgruppen"]] \
            or [t for t in self.inhaltstypen["inhaltstypen"] if rolle_kurz in t["zielgruppen"]]
        typ_by_uri = {t["uri"]: t for t in types}
        typ = typ_by_uri[state.pick_least_used(f"typ:{state.group}:{rolle_kurz}", list(typ_by_uri), rng)]
        niveaus = [n for n in self.niveaus["niveaus"] if quali in n["qualifizierung"]] or self.niveaus["niveaus"]
        if lf:  # Lernfelder gehören zur Ausbildung; das Ausbildungsjahr bestimmt die Stufe
            niveaus = [n for n in self.niveaus["niveaus"]
                       if n["id"] == ("ausbildung_grundstufe" if lf.get("jahr") == 1 else "ausbildung_fachstufe")]
        niveau = rng.choice(niveaus)

        seed: dict = {S.F_BEREICH: self.key}
        ctx_uri = CONTEXT_BASE + niveau["kontext"]
        S.set_controlled(seed, S.F_CONTEXT, [self.contexts.ref(ctx_uri)], S.provenance(
            "OpenEduHub educationalContext", uri=ctx_uri, license="CC0-1.0",
            method="aus dem Niveau abgeleitet (Ausbildung → Berufliche Bildung, Fortbildung → Fortbildung)"))
        m = b.get("mapping", {})
        vk = self.kldb.scheme
        prov = S.provenance("OpenEduHub-Vokabular kldb (KldB 2010, Fassung 2020)", uri=vk["uri"], license=vk["license"],
                            method=m.get("methode", "aus dem Beruf übernommen"))
        prov.update({x: m[x] for x in ("status", "konfidenz") if x in m})
        if vk.get("quellenangabe"):
            prov["quellenangabe"] = vk["quellenangabe"]
        S.set_controlled(seed, S.F_PROFESSION, b["berufsuntergruppe"], prov)
        lrt_src = self.inhaltstypen["quelle"]
        S.set_controlled(seed, S.F_LRT, [{"uri": typ["uri"], "label": typ["label"]}], S.provenance(
            lrt_src["name"], uri=lrt_src["uri"], license=lrt_src["lizenz"],
            method="passend zu Kompetenzart und Zielgruppe, je Gruppe rotierend (seltenster Typ zuerst)"))
        S.set_controlled(seed, S.F_ROLE, [self.roles.ref(role)], S.provenance(
            "OpenEduHub intendedEndUserRole", uri=self.roles.scheme["uri"], license="CC0-1.0", method="gewichtet zufällig"))
        seed[S.F_LANGUAGE] = opt["sprache"]["werte"]

        esco_lizenz = b.get("quelle", {}).get("lizenz")
        seed[S.F_BERUF] = {"uri": b["uri"], "label": b["label"], "isco_08": b["isco"],
                           "beschreibung": b["beschreibung"], "qualifizierung": quali,
                           **{e: b[e] for e in KLDB_EBENEN[1:]}}
        seed[S.F_PROVENANCE][S.F_BERUF] = S.provenance("ESCO-Berufe", uri=b["uri"], license=esco_lizenz,
                                                       method="Thema aus der Quelle")
        seed[S.F_PROVENANCE][S.F_BERUF]["qualifizierung"] = {
            "status": (m.get("qualifizierung") or {}).get("status"), "methode": "LLM-Mehrfachabstimmung"}
        status = {"kldb": m.get("status"), "qualifizierung": (m.get("qualifizierung") or {}).get("status")}
        if lf:
            p, q = topic["plan"], topic["plan"]["quelle"]
            seed[S.F_LERNFELD] = {"nr": lf["nr"], "titel": lf["titel"], "ausbildungsjahr": lf.get("jahr"),
                                  "zeitrichtwert_stunden": lf.get("stunden"),
                                  "rahmenlehrplan": {"titel": p["titel"], "datum": p["datum"], "url": p["url"]}}
            seed[S.F_PROVENANCE][S.F_LERNFELD] = S.provenance(q["name"], uri=p["url"], license=q["lizenz"],
                                                              method=p["mapping"]["methode"])
            seed[S.F_PROVENANCE][S.F_LERNFELD].update(quellenangabe=q["quellenangabe"], status=p["mapping"]["status"])
            # Thema: Lernfeld im Kontext des Ausbildungsberufs
            seed[S.F_THEMA] = {"id": topic["id"], "label_de": lf["titel"], "keywords_de": [lf["titel"]],
                               "beschreibung_de": f"Lernfeld {lf['nr']} des KMK-Rahmenlehrplans für den "
                                                  f"Ausbildungsberuf {p['titel']}"}
            seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(
                q["name"], uri=p["url"], license=q["lizenz"],
                method="Lernfeld des Rahmenlehrplans im zugeordneten ESCO-Beruf")
            status["rahmenlehrplan"] = p["mapping"]["status"]
        else:
            seed[S.F_KOMPETENZ] = {x: k[x] for x in ("uri", "label", "beschreibung", "art", "reichweite") if k.get(x)}
            seed[S.F_KOMPETENZ]["relation"] = topic["relation"]
            if k.get("oeh_uri"):
                seed[S.F_KOMPETENZ]["exactMatch"] = k["oeh_uri"]
            seed[S.F_PROVENANCE][S.F_KOMPETENZ] = S.provenance(
                "ESCO-Kompetenzen", uri=k["uri"], license=esco_lizenz,
                method=f"{topic['relation']}e Kompetenz des Berufs laut ESCO")
            # Thema für Vorschau, Suche und Prompts: Kompetenz im Kontext des Berufs
            seed[S.F_THEMA] = {"id": topic["id"], "label_de": k["label"], "beschreibung_de": k.get("beschreibung", ""),
                               "keywords_de": list(dict.fromkeys([k["label"], *k.get("alt_labels", [])[:4]]))}
            seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(
                "ESCO", uri="https://esco.ec.europa.eu", license=esco_lizenz,
                method="Kombination aus ESCO-Beruf und verknüpfter Kompetenz")
        seed[S.F_PROVENANCE][S.F_THEMA].update(status=status,
                                               belege={"erfuellt": sorted(self.belege_des_themas(topic))})

        seed[S.F_NIVEAU] = {"id": niveau["id"], "label": niveau["label"], "dqr": niveau["dqr"],
                            "exactMatch": niveau["exactMatch"]}
        seed[S.F_PROVENANCE][S.F_NIVEAU] = S.provenance(
            self.niveaus["quelle"]["name"],
            method="aus dem Ausbildungsjahr des Lernfelds (1. Jahr → Grundstufe, sonst Fachstufe)" if lf
            else "passend zum Qualifizierungsweg, gleichverteilt")
        seed[S.F_MEDIENFORM] = typ["medienform"]
        seed["synth:textsorte"] = typ["textsorte"]

        if rng.random() < opt["didaktische_funktion"]["anteil"]:
            seed[S.F_DIDAKTIK] = rng.choice(opt["didaktische_funktion"]["werte"])
        if rng.random() < opt["bearbeitungsdauer_minuten"]["anteil"]:
            seed[S.F_LEARNING_TIME] = rng.choice(opt["bearbeitungsdauer_minuten"]["werte"])
        if lf:
            seed[S.F_LERNORT] = "Berufsschule"  # Lernfelder sind der Lehrplan der Berufsschule
        elif rng.random() < opt["lernort"]["anteil"]:
            seed[S.F_LERNORT] = rng.choice(opt["lernort"]["werte"][self._weg(quali)])
        if rng.random() < opt["querschnittsthema"]["anteil"]:
            seed[S.F_QUERSCHNITT] = rng.choice(opt["querschnittsthema"]["werte"])
        return seed

    # --- Prompt-Werte ----------------------------------------------------------------------

    def inhaltstyp_kategorien(self) -> list[dict]:
        return [{"id": kurz_id(t["label"]), "name": t["label"], "uri": t["uri"]}
                for t in self.inhaltstypen["inhaltstypen"]]

    def prompt_values(self, seed: dict) -> dict:
        from .lernfelder import lernfeld_text

        t, b, k = seed[S.F_THEMA], seed[S.F_BERUF], seed.get(S.F_KOMPETENZ) or {}
        lf = seed.get(S.F_LERNFELD)
        fortbildung = self._weg(b.get("qualifizierung", "ausbildung")) == "fortbildung"
        lernende = "Teilnehmende einer beruflichen Fortbildung" if fortbildung else "Auszubildende"
        lehrende = "Lehrkräfte und Dozierende in der Fortbildung" if fortbildung else \
            "Lehrkräfte an Berufsschulen und Ausbildende in Betrieben"
        return {
            "rolle": "eine erfahrene Lehrkraft in der beruflichen Fortbildung" if fortbildung
            else "eine erfahrene Lehrkraft an einer Berufsschule mit Erfahrung in der betrieblichen Ausbildung",
            "sprache": ", ".join({"de": "Deutsch", "en": "Englisch"}.get(x, x) for x in seed.get(S.F_LANGUAGE, [])),
            "textsorte": seed["synth:textsorte"],
            "inhaltstyp": seed[S.F_LRT + "_DISPLAYNAME"][0],
            "medienform": seed[S.F_MEDIENFORM],
            "thema": t["label_de"],
            "thema_beschreibung": t.get("beschreibung_de", ""),
            "thema_keywords": ", ".join(t.get("keywords_de", [])),
            "kompetenzart": {"wissen": "Kenntnisse", "faehigkeit": "Fähigkeit"}.get(k.get("art"), ""),
            "beruf": b["label"],
            "beruf_beschreibung": b.get("beschreibung", ""),
            "fach": ", ".join(seed[S.F_PROFESSION + "_DISPLAYNAME"]),
            "bildungsstufe": seed[S.F_CONTEXT + "_DISPLAYNAME"][0],
            "niveau": seed[S.F_NIVEAU]["label"],
            "zielgruppe": {"learner": lernende, "teacher": lehrende}.get(
                seed[S.F_ROLE][0].rsplit("/", 1)[-1], seed[S.F_ROLE + "_DISPLAYNAME"][0]),
            "didaktische_funktion": seed.get(S.F_DIDAKTIK, ""),
            "bearbeitungsdauer": seed.get(S.F_LEARNING_TIME, ""),
            "lernort": seed.get(S.F_LERNORT, ""),
            "querschnittsthema": seed.get(S.F_QUERSCHNITT, ""),
            "lernfeld": lernfeld_text({**lf, "jahr": lf.get("ausbildungsjahr"), "stunden": lf.get("zeitrichtwert_stunden")})
            if lf else "",
            "rahmenlehrplan": lf["rahmenlehrplan"]["titel"] if lf else "",
        }
