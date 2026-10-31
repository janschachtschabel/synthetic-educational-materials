"""Bildungsbereich Allgemeinbildende Schule (Primarstufe, Sekundarstufe I und II).

Themen: die Themenbäume der WLO-Fachportale (OEH-Vokabular oehTopics), je Thema per Mehrfachabstimmung als
Unterrichtsthema oder Thema für Lehrkräfte eingestuft und den Schulstufen zugeordnet, in denen es unterrichtet
wird; Gegenprobe über die Bildungsstufen der WLO-Datensätze. Dazu feinere Themen aus den Keywords der
WLO-Schuldatensätze, soweit sie exakt einem GND-Sachbegriff entsprechen (Herkunft „wlo_keyword“), mit Fach,
Themenart und Stufen per Abstimmung und, wo belegt, einem Oberthema im Themenbaum. Die Schulstufe steht als Metadatum im Seed
(ccm:educationalcontext: Primarstufe, Sekundarstufe I oder II) und ist über auswahl_optionen filterbar.
Fach: OEH discipline aus dem Themenbaum.
"""

from __future__ import annotations

import json
import random
from functools import cached_property

from ... import schema as S
from ...paths import MAPPING_DIR
from ...vocabs import load as load_vocab
from ..base import Bildungsbereich, MappingSchritt, SeedState
from ..hochschule import kurz_id, load_jsonl

DIR = MAPPING_DIR / "schule"
CONTEXT_BASE = "http://w3id.org/openeduhub/vocabs/educationalContext/"
STUFEN = ["primar", "sek1", "sek2"]
STUFEN_LABEL = {"primar": "Primarstufe", "sek1": "Sekundarstufe I", "sek2": "Sekundarstufe II"}
# Fremdsprachenportale: Zielsprache zusätzlich als Sprache des Materials
ZIELSPRACHE = {"Englisch": "en", "Französisch": "fr", "Spanisch": "es", "Türkisch": "tr"}

__all__ = ["Schule", "DIR", "STUFEN", "load_jsonl"]


class Schule(Bildungsbereich):
    key = "schule"
    label = "Schule"
    balance_options = {
        "thema": "jedes Thema etwa gleich oft",
        "portal": "jedes WLO-Fachportal etwa gleich oft (kleine Portale wiederholen ihre Themen oft)",
        "fach": "jedes Schulfach (OEH discipline) etwa gleich oft",
        "stufe": "Primarstufe, Sekundarstufe I und II etwa gleich oft (maßgeblich: erste Stufe des Themas)",
    }
    default_balance = "thema"
    mapping_folgen = {"themen": ["themen", "wlo", "gnd", "art", "stufen"],
                      "keywords": ["keywords", "kw_gnd", "kw_bedeutung", "kw_art", "kw_fach", "kw_stufen"],
                      # braucht themen_schule.jsonl: bei einem Neuaufbau nach den Themen ausführen
                      "bildungsstandards": ["bista", "bista_zusammenfuehren"]}
    mapping_abschluss = ["zusammenfuehren"]

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import bildungsstandards as bs
        from . import build as b

        S_ = MappingSchritt
        return [
            S_("themen", "Themenbäume laden", "WLO-Themenbäume (oehTopics) mit Fachportal, Pfad, Fach und Keywords",
               b.build_themen, llm=False),
            S_("wlo", "WLO-Stufen", "Bildungsstufen der WLO-Datensätze je Thema (--wlo)", b.build_wlo, llm=False),
            S_("gnd", "GND-Abgleich", "Themenlabels exakt auf GND-Sachbegriffe (lobid)", b.build_gnd, llm=False),
            S_("art", "Themenart abstimmen", "Unterrichtsthema, Lehrkräftethema oder Sammelknoten (drei Stimmen)",
               b.build_art),
            S_("stufen", "Schulstufen abstimmen", "Primarstufe, Sek I, Sek II (drei Stimmen)", b.build_stufen),
            S_("keywords", "WLO-Keywords je Fach", "Keywords der Schuldatensätze mit Fach und Stufe zählen (--wlo)",
               b.build_keywords, llm=False),
            S_("kw_gnd", "Keywords → GND", "Keywords exakt auf GND-Sachbegriffe; Themenbaum-Treffer werden Belege",
               b.build_kw_gnd, llm=False),
            S_("kw_bedeutung", "Keyword-Themen: Bedeutung", "Nur Synonym-Treffer (z. B. Abkürzungen): gleiche "
               "Bedeutung wie der GND-Begriff? (drei Stimmen)", b.build_kw_bedeutung),
            S_("kw_art", "Keyword-Themen: Themenart", "Unterrichtsthema, Lehrkräftethema oder ungeeignet (drei Stimmen)",
               b.build_kw_art),
            S_("kw_fach", "Keyword-Themen: Schulfach", "Fach aus den Fächern der WLO-Datensätze (drei Stimmen)",
               b.build_kw_fach),
            S_("kw_stufen", "Keyword-Themen: Schulstufen", "Primarstufe, Sek I, Sek II (drei Stimmen)",
               b.build_kw_stufen),
            S_("bista", "Bildungsstandards abstimmen", "Unterrichtsthemen auf Kompetenzbereiche, Leitideen und "
               "Basiskonzepte der KMK-Bildungsstandards (drei Stimmen)", bs.build_bista),
            S_("bista_zusammenfuehren", "Bildungsstandards zusammenführen", "bildungsstandards.jsonl",
               bs.zusammenfuehren, llm=False),
            S_("zusammenfuehren", "Zusammenführen", "themen_schule.jsonl, Prüfliste, Korrekturen (läuft zuletzt)",
               b.zusammenfuehren, llm=False),
        ]

    def mapping_statistik(self) -> dict:
        from . import build

        return build.statistik() if (DIR / "themen_schule.jsonl").exists() else {}

    # --- Auswahl der Themen ------------------------------------------------------------------

    def auswahl_optionen(self) -> dict[str, dict]:
        status = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                  "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                  "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
        std = ["gesichert", "wahrscheinlich", "redaktionell"]
        return {
            "herkunft": {"label": "Herkunft der Themen", "beschreibung": "Themenbaum oder Schlagwörter der WLO-Materialien",
                         "werte": {"themenbaum": "Thema aus den WLO-Themenbäumen (Lehrplanthemen)",
                                   "wlo_keyword": "feineres Thema aus WLO-Keywords, als GND-Sachbegriff normiert"},
                         "standard": ["themenbaum", "wlo_keyword"]},
            "stufen": {"label": "Schulstufen", "beschreibung": "nur Seeds dieser Stufen (z. B. nur Sekundarstufe II)",
                       "werte": {k: v for k, v in STUFEN_LABEL.items()}, "standard": list(STUFEN)},
            "art": {"label": "Themenart", "beschreibung": "Abstimmung",
                    "werte": {"unterrichtsthema": "Inhalt für Schülerinnen und Schüler",
                              "lehrkraftthema": "Didaktik, Methodik, Organisation für Lehrkräfte"},
                    "standard": ["unterrichtsthema", "lehrkraftthema"]},
            "stufen_status": {"label": "Status der Schulstufen", "beschreibung": "Abstimmung", "werte": status,
                              "standard": std},
            "art_status": {"label": "Status der Themenart", "beschreibung": "Abstimmung", "werte": status,
                           "standard": std},
            "fach_status": {"label": "Status des Schulfachs (nur Keyword-Themen)", "beschreibung": "Abstimmung",
                            "werte": status, "standard": std},
            "belege": {"label": "Belege (mindestens einer)", "beschreibung": "leer = keine Anforderung",
                       "werte": {"wlo": "WLO-Datensätze zu diesem Thema",
                                 "wlo_stufe": "Schulstufe durch die Bildungsstufen der WLO-Datensätze bestätigt",
                                 "wlo_keyword": "Thema steht als Keyword in WLO-Datensätzen",
                                 "gnd": "Thema entspricht exakt einem GND-Sachbegriff"},
                       "standard": []},
        }

    @staticmethod
    def belege_des_themas(topic: dict) -> set[str]:
        out = set()
        if (topic.get("belege") or {}).get("wlo"):
            out.add("wlo")
        if topic.get("herkunft") == "wlo_keyword" or (topic.get("belege") or {}).get("wlo_keyword"):
            out.add("wlo_keyword")
        if (topic.get("mapping") or {}).get("wlo_bestaetigt"):
            out.add("wlo_stufe")
        if topic.get("gnd"):
            out.add("gnd")
        return out

    def themen_auswahl(self, auswahl=None) -> list[dict]:
        auswahl = self.auswahl_pruefen(auswahl)
        self._stufen_auswahl = list(auswahl["stufen"])  # make_seed zieht die Stufe nur aus der Auswahl
        return [t for t in self.topics() if self.passt(t, auswahl)]

    kombination_label = "Thema × Fach × Stufe"

    def kombinationen(self, themen: list[dict], auswahl: dict[str, list[str]] | None = None) -> int:
        """Je Thema jedes Fach mit jeder gewählten Stufe (Keyword-Themen haben bis zu zwei Fächer)."""
        stufen = set(self.auswahl_pruefen(auswahl)["stufen"])
        return sum(max(1, len(t.get("faecher") or [])) * max(1, len(set(t["stufen"]) & stufen)) for t in themen)

    def passt(self, topic: dict, auswahl: dict[str, list[str]]) -> bool:
        m = topic.get("mapping") or {}
        if not set(topic["stufen"]) & set(auswahl["stufen"]) or topic["art"] not in auswahl["art"]:
            return False
        if m.get("status", "gesichert") not in auswahl["stufen_status"]:
            return False
        if (m.get("art") or {}).get("status", "gesichert") not in auswahl["art_status"]:
            return False
        if topic.get("herkunft", "themenbaum") not in auswahl["herkunft"]:
            return False
        if (m.get("fach") or {}).get("status", "gesichert") not in auswahl["fach_status"]:
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
        path = DIR / "themen_schule.jsonl"
        out = load_jsonl(path) if path.exists() else []
        if not out:
            raise FileNotFoundError(f"Keine Themen in {DIR}. Erst die Mappings bauen: python -m sem mappings schule")
        return out

    def balance_key(self, topic: dict, balance: str) -> str:
        if balance == "thema":
            return topic["id"]
        if balance == "portal":
            return topic["portal"]
        if balance == "fach":
            return (topic["faecher"] or [topic["portal"]])[0]
        return topic["stufen"][0]

    # --- Seed ---------------------------------------------------------------------------

    def make_seed(self, topic: dict, rng: random.Random, state: SeedState) -> dict:
        opt = self.optionen
        art = topic["art"]
        erlaubt = getattr(self, "_stufen_auswahl", STUFEN)
        stufe = state.pick_least_used(f"stufe:{topic['id']}", [s for s in topic["stufen"] if s in erlaubt]
                                      or topic["stufen"], rng)
        role_w = opt["zielgruppe"]["werte"][art]
        role = rng.choices([r["uri"] for r in role_w], weights=[r["gewicht"] for r in role_w])[0]
        rolle = role.rsplit("/", 1)[-1]

        def ok(t):
            return (art in t["arten"] and rolle in t["zielgruppen"] and (not t["stufen"] or stufe in t["stufen"])
                    and (not t["portale"] or topic["portal"] in t["portale"]))

        types = [t for t in self.inhaltstypen["inhaltstypen"] if ok(t)] or \
            [t for t in self.inhaltstypen["inhaltstypen"] if art in t["arten"] and rolle in t["zielgruppen"]]
        typ_by_uri = {t["uri"]: t for t in types}
        typ = typ_by_uri[state.pick_least_used(f"typ:{state.group}:{stufe}:{rolle}", list(typ_by_uri), rng)]
        niveau = rng.choice([n for n in self.niveaus["niveaus"] if n["stufe"] == stufe])

        seed: dict = {S.F_BEREICH: self.key}
        ctx = CONTEXT_BASE + niveau["kontext"]
        S.set_controlled(seed, S.F_CONTEXT, [self.contexts.ref(ctx)], S.provenance(
            "OpenEduHub educationalContext", uri=ctx, license="CC0-1.0",
            method="Schulstufe des Themas (Abstimmung), je Thema rotierend"))
        faecher = [u for u in topic["faecher"] if u in self.disciplines.by_uri]
        if faecher:
            S.set_controlled(seed, S.F_TAXON, [self.disciplines.ref(u) for u in faecher], S.provenance(
                "OpenEduHub discipline", uri=self.disciplines.scheme["uri"], license="CC0-1.0",
                method="relatedMatch des Themas im WLO-Themenbaum" if topic.get("herkunft", "themenbaum") == "themenbaum"
                else "Abstimmung aus den Fächern der WLO-Datensätze mit diesem Keyword"))
        m = topic.get("mapping", {})
        q = topic["quelle"]
        if topic.get("herkunft", "themenbaum") == "themenbaum":
            S.set_controlled(seed, S.F_CURRICULUM, [{"uri": topic["id"], "label": topic["label_de"]}], {
                **S.provenance(q["name"], uri=q["uri"], license=q["lizenz"], method="Thema aus der Quelle"),
                "status": {"stufen": m.get("status"), "art": (m.get("art") or {}).get("status")}})
        elif topic.get("eltern"):
            S.set_controlled(seed, S.F_CURRICULUM, [{"uri": topic["eltern"]["uri"], "label": topic["eltern"]["label"]}],
                             S.provenance("WirLernenOnline-Themenbäume (OEH-Vokabular oehTopics)",
                                          uri="http://w3id.org/openeduhub/vocabs/oeh-topics/", license="CC0-1.0",
                                          method="Oberthema: häufigste Themenbaum-Zuordnung der WLO-Datensätze "
                                                 "mit diesem Keyword"))
        lrt = self.inhaltstypen["quelle"]
        S.set_controlled(seed, S.F_LRT, [{"uri": typ["uri"], "label": typ["label"]}], S.provenance(
            lrt["name"], uri=lrt["uri"], license=lrt["lizenz"],
            method="passend zu Themenart, Zielgruppe, Stufe und Fachportal, je Gruppe rotierend"))
        S.set_controlled(seed, S.F_ROLE, [self.roles.ref(role)], S.provenance(
            "OpenEduHub intendedEndUserRole", uri=self.roles.scheme["uri"], license="CC0-1.0",
            method="gewichtet zufällig je Themenart"))
        seed[S.F_LANGUAGE] = list(dict.fromkeys(opt["sprache"]["werte"] + ([ZIELSPRACHE[topic["portal"]]]
                                                                           if topic["portal"] in ZIELSPRACHE else [])))

        seed[S.F_THEMA] = {"id": topic["id"], "label_de": topic["label_de"], "beschreibung_de": topic.get("definition", ""),
                           "keywords_de": list(dict.fromkeys([topic["label_de"], *topic.get("keywords", [])[:6]])),
                           "fachportal": topic["portal"], "pfad": topic["pfad"], "art": art, "stufen": topic["stufen"]}
        if topic.get("gnd"):
            seed[S.F_THEMA]["gnd"] = topic["gnd"]
        seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(q["name"], uri=q["uri"], license=q["lizenz"],
                                                       method=m.get("methode", "Thema aus der Quelle"))
        seed[S.F_PROVENANCE][S.F_THEMA].update(
            status={"stufen": m.get("status"), "art": (m.get("art") or {}).get("status"),
                    **({"fach": m["fach"]["status"]} if m.get("fach") else {})},
            belege={"erfuellt": sorted(self.belege_des_themas(topic))})

        from .bildungsstandards import fuer_seed

        bista = fuer_seed(topic["id"], topic["portal"], stufe) if art == "unterrichtsthema" else None
        if bista:
            g = bista.pop("status")
            seed[S.F_BILDUNGSSTANDARDS] = bista
            seed[S.F_PROVENANCE][S.F_BILDUNGSSTANDARDS] = {
                **S.provenance(bista["dokument"], uri=bista["uri"],
                               license="urheberrechtlich geschützt; übernommen nur Gliederung und Titel",
                               method="LLM-Mehrfachabstimmung über die inhaltliche Gliederung"), "status": g}

        seed[S.F_NIVEAU] = {"id": niveau["id"], "label": niveau["label"], "stufe": STUFEN_LABEL[stufe],
                            "exactMatch": niveau["exactMatch"]}
        seed[S.F_PROVENANCE][S.F_NIVEAU] = S.provenance(self.niveaus["quelle"]["name"],
                                                        method="gleichverteilt innerhalb der Schulstufe")
        seed[S.F_MEDIENFORM] = typ["medienform"]
        seed["synth:textsorte"] = typ["textsorte"]
        if rng.random() < opt["didaktische_funktion"]["anteil"]:
            seed[S.F_DIDAKTIK] = rng.choice(opt["didaktische_funktion"]["werte"])
        if rng.random() < opt["bearbeitungsdauer_minuten"]["anteil"]:
            seed[S.F_LEARNING_TIME] = rng.choice(opt["bearbeitungsdauer_minuten"]["werte"])
        afb = opt["anforderungsbereich"]
        if art == "unterrichtsthema" and rng.random() < afb["anteil"]:
            ids = afb["primar"] if stufe == "primar" else list(afb["werte"])
            seed["synth:anforderungsbereich"] = afb["werte"][rng.choice(ids)]
        if rng.random() < opt["sozialform"]["anteil"]:
            seed["synth:sozialform"] = rng.choice(opt["sozialform"]["werte"])
        foe = opt["foerderschwerpunkt"]
        if art == "unterrichtsthema" and stufe in foe["stufen"] and rng.random() < foe["anteil"]:
            # Material für eine Förderschule bzw. den sonderpädagogischen Förderbedarf: zweite Bildungsstufe
            seed[S.F_FOERDERSCHWERPUNKT] = rng.choice(foe["werte"])
            ctx_f = CONTEXT_BASE + "foerderschule"
            seed[S.F_CONTEXT].append(ctx_f)
            seed[S.F_CONTEXT + "_DISPLAYNAME"].append(self.contexts.ref(ctx_f)["label"])
            seed[S.F_PROVENANCE][S.F_FOERDERSCHWERPUNKT] = S.provenance(
                foe["quelle"]["name"], uri=foe["quelle"]["uri"], license=None,
                method=f"optional ({foe['anteil']:.0%} der Unterrichtsthemen in {', '.join(foe['stufen'])}), "
                       "gleichverteilt; Bildungsstufe Förderschule ergänzt")
        return seed

    # --- Prompt-Werte ----------------------------------------------------------------------

    def inhaltstyp_kategorien(self) -> list[dict]:
        return [{"id": kurz_id(t["label"]), "name": t["label"], "uri": t["uri"]}
                for t in self.inhaltstypen["inhaltstypen"]]

    def prompt_values(self, seed: dict) -> dict:
        t = seed[S.F_THEMA]
        rolle = seed[S.F_ROLE][0].rsplit("/", 1)[-1]
        sprachen = {"de": "Deutsch", "en": "Englisch", "fr": "Französisch", "es": "Spanisch", "tr": "Türkisch"}
        stufe = seed[S.F_CONTEXT + "_DISPLAYNAME"][0]
        return {
            "rolle": f"eine erfahrene Lehrkraft für {t['fachportal']}",
            "sprache": ", ".join(sprachen.get(x, x) for x in seed.get(S.F_LANGUAGE, [])),
            "textsorte": seed["synth:textsorte"],
            "inhaltstyp": seed[S.F_LRT + "_DISPLAYNAME"][0],
            "medienform": seed[S.F_MEDIENFORM],
            "thema": t["label_de"],
            "thema_pfad": " > ".join([t["fachportal"], *t.get("pfad", []), t["label_de"]]),
            "thema_beschreibung": t.get("beschreibung_de", ""),
            "thema_keywords": ", ".join(t.get("keywords_de", [])),
            "fach": ", ".join(seed.get(S.F_TAXON + "_DISPLAYNAME", [])) or t["fachportal"],
            "bildungsstufe": stufe,
            "niveau": seed[S.F_NIVEAU]["label"],
            "zielgruppe": {"learner": f"Schülerinnen und Schüler ({seed[S.F_NIVEAU]['label']})",
                           "teacher": "Lehrkräfte"}.get(rolle, seed[S.F_ROLE + "_DISPLAYNAME"][0]),
            "zielsprache": sprachen.get(ZIELSPRACHE.get(t["fachportal"], ""), ""),
            "didaktische_funktion": seed.get(S.F_DIDAKTIK, ""),
            "bearbeitungsdauer": seed.get(S.F_LEARNING_TIME, ""),
            "anforderungsbereich": seed.get("synth:anforderungsbereich", ""),
            "sozialform": seed.get("synth:sozialform", ""),
            "foerderschwerpunkt": seed.get(S.F_FOERDERSCHWERPUNKT, ""),
            "bildungsstandards": "; ".join(e["label"] for e in (seed.get(S.F_BILDUNGSSTANDARDS) or {}).get("eintraege", [])),
        }
