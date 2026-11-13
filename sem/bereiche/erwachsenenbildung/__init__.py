"""Bildungsbereich Erwachsenenbildung (allgemeine und berufliche Weiterbildung in Kursform, z. B. Volkshochschule).

Fach: Programmbereiche und Fachgebiete der Volkshochschul-Statistik (DIE, Fachgebietsgliederung seit 2018).
Themen: GND-Sachbegriffe aus LLM-Themenlisten je Fachgebiet, per Mehrfachabstimmung als Kursthema oder Thema für
Kursleitende eingestuft und ein bis zwei Fachgebieten zugeordnet. Fremdsprachen-Kursthemen gelten für alle Sprachen
(Ziel „4.fs“); die konkrete Sprache wird beim Seed gezogen. Niveau je Programmbereich (GER, Alpha-Level, Abschluss).
Die berufliche Aufstiegsfortbildung (Meister, Fachwirt) gehört zur Berufsbildung.
Weitere Themenquelle: Lernergebnisse aus DigComp 3.0 (CC BY 4.0, eigene Übersetzung, siehe digcomp.py).
Themen aus urheberrechtlich geschützten Gliederungen (Rahmencurriculum Integrationskurse, Grundbildung) stehen
nur lokal in data/lokal/erwachsenenbildung/themen_lokal.jsonl und kommen dazu, wenn die Datei existiert.
"""

from __future__ import annotations

import json
import random
import warnings
from functools import cache, cached_property

from ... import faecher, gnd_ergaenzung
from ... import schema as S
from ...paths import MAPPING_DIR, lokal_pfad
from ...vocabs import load as load_vocab
from ..base import Bildungsbereich, MappingSchritt, SeedState
from ..hochschule import load_jsonl

DIR = MAPPING_DIR / "erwachsenenbildung"
CONTEXT_URI = "http://w3id.org/openeduhub/vocabs/educationalContext/erwachsenenbildung"
F_FACHGEBIET = "synth:fachgebiet_vhs"
F_PROGRAMMBEREICH = "synth:programmbereich_vhs"
ZIEL_FS = "4.fs"
THEMEN_LOKAL = "erwachsenenbildung/themen_lokal.jsonl"  # unter data/lokal/
# Herkünfte aus urheberrechtlich geschützten Gliederungen: Themen nur lokal
LOKALE_HERKUNFT = ("rahmencurriculum", "grundbildung")
# Fremdsprachen der Statistik (ohne Deutsch als Fremd-/Muttersprache, Dialekte, Gebärdensprache, Sammelposition)
FREMDSPRACHEN = [f"4.{i:02d}" for i in range(1, 27) if i not in (4, 5)]
SPRACHNAMEN = {"ar": "Arabisch", "zh": "Chinesisch", "da": "Dänisch", "de": "Deutsch", "en": "Englisch", "fi": "Finnisch",
               "fr": "Französisch", "it": "Italienisch", "ja": "Japanisch", "la": "Latein", "el": "Neugriechisch",
               "he": "Neuhebräisch", "nl": "Niederländisch", "no": "Norwegisch", "fa": "Persisch", "pl": "Polnisch",
               "pt": "Portugiesisch", "ru": "Russisch", "sv": "Schwedisch", "hr": "Bosnisch/Kroatisch/Serbisch",
               "es": "Spanisch", "cs": "Tschechisch", "tr": "Türkisch", "hu": "Ungarisch"}

__all__ = ["Erwachsenenbildung", "DIR", "FREMDSPRACHEN", "LOKALE_HERKUNFT", "THEMEN_LOKAL", "ZIEL_FS", "lade_themen",
           "load_jsonl", "sortiert", "systematik"]


@cache
def systematik() -> dict:
    return json.loads((DIR / "programmbereiche.json").read_text(encoding="utf-8"))


def sortiert(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["fachgebiet"][0]["notation"], r["label_de"].casefold()))


def lade_themen() -> list[dict]:
    """Themen aus dem Repo (Mapping-Ergebnis und DigComp-Lernergebnisse) plus die lokalen Themen (Warnung, wenn sie
    fehlen)."""
    from . import digcomp

    path, lokal = DIR / "themen_erwachsenenbildung.jsonl", lokal_pfad(THEMEN_LOKAL)
    out = load_jsonl(path) if path.exists() else []
    if out:
        out += digcomp.themen(systematik())
    if lokal.exists():
        out += load_jsonl(lokal)
    elif out:
        warnings.warn(f"{lokal} fehlt: keine Themen aus Rahmencurriculum Integrationskurse und Grundbildung "
                      "(siehe data/README.md, data/lokal/)")
    return sortiert(out)


class Erwachsenenbildung(Bildungsbereich):
    key = "erwachsenenbildung"
    label = "Erwachsenenbildung"
    balance_options = {
        "programmbereich": "jeder der sieben Programmbereiche der Volkshochschul-Statistik etwa gleich oft",
        "fachgebiet": "jedes Fachgebiet etwa gleich oft (Fremdsprachen als ein Fachgebiet)",
        "thema": "jedes Thema etwa gleich oft",
        "art": "Kursthemen und Themen für Kursleitende etwa gleich oft",
    }
    default_balance = "programmbereich"
    mapping_folgen = {"themen": ["kandidaten_llm", "gnd", "wlo", "kandidaten", "eignung", "fachgebiete"],
                      "digcomp": ["digcomp", "digcomp_glossar", "digcomp_uebersetzen"]}
    mapping_abschluss = ["zusammenfuehren"]

    def mapping_schritte(self) -> list[MappingSchritt]:
        from . import build as b
        from . import digcomp as d

        S_ = MappingSchritt
        return [
            S_("kandidaten_llm", "Themenkandidaten", "typische Kursthemen je Fachgebiet aus zwei Perspektiven",
               b.build_kandidaten_llm),
            S_("gnd", "GND-Abgleich", "Kandidaten exakt auf GND-Sachbegriffe (lobid)", b.build_gnd, llm=False),
            S_("wlo", "WLO-Belege", "Keywords der WLO-Datensätze in der Erwachsenenbildung, nur als Beleg (--wlo)",
               b.build_wlo, llm=False),
            S_("kandidaten", "Kandidaten", "ähnlichste Fachgebiete je Begriff (lokale Embeddings)", b.build_kandidaten,
               llm=False),
            S_("eignung", "Eignung abstimmen", "Kursthema, Thema für Kursleitende oder ungeeignet (drei Stimmen)",
               b.build_eignung),
            S_("fachgebiete", "Fachgebiete abstimmen", "ein bis zwei Fachgebiete je Thema (drei Stimmen)",
               b.build_fachgebiete),
            S_("zusammenfuehren", "Zusammenführen", "themen_erwachsenenbildung.jsonl, Prüfliste, Korrekturen "
               "(läuft zuletzt)", b.zusammenfuehren, llm=False),
            S_("digcomp", "DigComp laden", "DigComp-3.0-Data-Supplement lesen, Errata anwenden (--digcomp)",
               d.build_digcomp, llm=False),
            S_("digcomp_glossar", "DigComp-Glossar", "deutsche Fachbegriffe für das Glossar (Vorgabe der Übersetzung)",
               d.build_digcomp_glossar),
            S_("digcomp_uebersetzen", "DigComp übersetzen", "Bereiche, Kompetenzen und Lernergebnisse ins Deutsche "
               "(eigene, nicht amtliche Übersetzung)", d.build_digcomp_uebersetzen),
        ]

    def mapping_statistik(self) -> dict:
        from . import build

        return build.statistik() if (DIR / "themen_erwachsenenbildung.jsonl").exists() else {}

    # --- Auswahl der Themen ------------------------------------------------------------------

    def auswahl_optionen(self) -> dict[str, dict]:
        status = {"gesichert": "alle Stimmen einig", "wahrscheinlich": "Mehrheit der Stimmen",
                  "unsicher": "keine Mehrheit oder Ersatzzuordnung, steht auf der Prüfliste",
                  "redaktionell": "von Hand korrigiert (korrekturen.csv)"}
        std = ["gesichert", "wahrscheinlich", "redaktionell"]
        return {
            "programmbereiche": {"label": "Programmbereiche", "beschreibung": "Volkshochschul-Statistik",
                                 "werte": {p["notation"]: p["label"] for p in systematik()["programmbereiche"]},
                                 "standard": [p["notation"] for p in systematik()["programmbereiche"]]},
            "quellen": {"label": "Themenquellen", "beschreibung": "woher die Themen stammen",
                        "werte": {"gnd": "GND-Sachbegriffe aus Themenlisten je Fachgebiet (Abstimmung)",
                                  "rahmencurriculum": "Handlungsfelder des Rahmencurriculums für Integrationskurse "
                                                      "(Sprachen)",
                                  "schulthemen": "Unterrichtsthemen der Schule in Deutsch, Mathematik, Englisch "
                                                 "(Schulabschlüsse)",
                                  "grundbildung": "Gliederung der lea.-Alpha-Levels und DVV-Rahmencurricula Lesen, "
                                                  "Schreiben, Rechnen (Grundbildung)",
                                  "digcomp": "Lernergebnisse des europäischen Kompetenzrahmens DigComp 3.0 (digitale "
                                             "Kompetenzen, eigene Übersetzung)"},
                        "standard": ["gnd", "rahmencurriculum", "schulthemen", "grundbildung", "digcomp"]},
            "art": {"label": "Themenart", "beschreibung": "Abstimmung",
                    "werte": {"kursthema": "Kursthema für Teilnehmende",
                              "kursleitungsthema": "Thema für Kursleitende"},
                    "standard": ["kursthema", "kursleitungsthema"]},
            "eignung_status": {"label": "Status der Eignung", "beschreibung": "Abstimmung", "werte": status,
                               "standard": std},
            "fachgebiet_status": {"label": "Status des Fachgebiets", "beschreibung": "Abstimmung", "werte": status,
                                  "standard": std},
            "belege": {"label": "Belege (mindestens einer)", "beschreibung": "leer = keine Anforderung",
                       "werte": {"wlo": "Begriff als WLO-Keyword in der Erwachsenenbildung (mindestens zwei Datensätze)",
                                 "llm_bestaetigt": "Fachgebiet der Kandidatenliste durch die Abstimmung bestätigt"},
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
        if topic["programmbereich"][0]["notation"] not in auswahl["programmbereiche"] or topic["art"] not in auswahl["art"]:
            return False
        if topic.get("herkunft", "gnd") not in auswahl["quellen"]:
            return False
        if (m.get("eignung") or {}).get("status", "gesichert") not in auswahl["eignung_status"]:
            return False
        if m.get("status", "gesichert") not in auswahl["fachgebiet_status"]:
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
        # Motiv-Ersatz und ergänzte Beschreibungen (data/mappings/gnd_ergaenzungen.jsonl), falls vorhanden
        return gnd_ergaenzung.anwenden(self._topics_roh(), self.key)

    def _topics_roh(self) -> list[dict]:
        out = lade_themen()
        if not out:
            raise FileNotFoundError(f"Keine Themen in {DIR}. Erst die Mappings bauen: "
                                    "python -m sem mappings erwachsenenbildung")
        return out

    def balance_key(self, topic: dict, balance: str) -> str:
        if balance == "thema":
            return topic["id"]
        if balance == "programmbereich":
            return topic["programmbereich"][0]["notation"]
        if balance == "fachgebiet":
            return topic["fachgebiet"][0]["notation"]
        return topic["art"]

    # --- Seed ---------------------------------------------------------------------------

    kombination_label = "Thema × Fremdsprache"

    def kombinationen(self, themen: list[dict], auswahl: dict[str, list[str]] | None = None) -> int:
        """Themen; Fremdsprachen-Themen zählen je konkreter Sprache (sie wird je Seed rotiert)."""
        return sum(len(FREMDSPRACHEN) if any(f["notation"] == ZIEL_FS for f in t["fachgebiet"]) else 1
                   for t in themen)

    def make_seed(self, topic: dict, rng: random.Random, state: SeedState) -> dict:
        opt = self.optionen
        s = systematik()
        fg_by = {f["notation"]: f for f in s["fachgebiete"]}
        art = topic["art"]
        # Fremdsprachen: konkrete Sprache je Thema rotierend
        fachgebiete = []
        for f in topic["fachgebiet"]:
            if f["notation"] == ZIEL_FS:
                n = state.pick_least_used(f"sprache:{topic['id']}", FREMDSPRACHEN, rng)
                fachgebiete.append({"uri": fg_by[n]["uri"], "label": fg_by[n]["label"], "notation": n})
            else:
                fachgebiete.append({"uri": f["uri"], "label": f["label"], "notation": f["notation"]})
        haupt = fachgebiete[0]["notation"]
        pb = haupt[0]

        role_w = opt["zielgruppe"]["werte"][art]
        role = rng.choices([r["uri"] for r in role_w], weights=[r["gewicht"] for r in role_w])[0]
        rolle = role.rsplit("/", 1)[-1]
        types = [t for t in self.inhaltstypen["inhaltstypen"] if art in t["arten"] and rolle in t["zielgruppen"]
                 and (not t["programmbereiche"] or pb in t["programmbereiche"])] or \
            [t for t in self.inhaltstypen["inhaltstypen"] if art in t["arten"] and rolle in t["zielgruppen"]]
        typ_by_uri = {t["uri"]: t for t in types}
        typ = typ_by_uri[state.pick_least_used(f"typ:{state.group}:{rolle}", list(typ_by_uri), rng)]
        niveaus = [n for n in self.niveaus["niveaus"] if pb in n["programmbereiche"]
                   and (not n.get("fachgebiete") or haupt in n["fachgebiete"])]
        dc = topic.get("digcomp")
        if dc:  # DigComp: Niveau aus dem DigComp-Niveau (Handmapping), nicht gezogen
            from .digcomp import fachgebiete as dc_hand

            ziel = dc_hand()["niveaus"][dc["niveau"]]["eb_niveau"]
            niveaus = [n for n in self.niveaus["niveaus"] if n["id"] == ziel]
        niveau = rng.choice(niveaus) if niveaus else None

        seed: dict = {S.F_BEREICH: self.key}
        S.set_controlled(seed, S.F_CONTEXT, [self.contexts.ref(CONTEXT_URI)], S.provenance(
            "OpenEduHub educationalContext", uri=CONTEXT_URI, license="CC0-1.0", method="fest codiert"))
        q = s["quelle"]
        m = topic.get("mapping", {})
        prov = {**S.provenance(q["name"], uri=q["uri"], license=q["lizenz"], method=m.get("methode", "aus dem Thema")),
                **{k: m[k] for k in ("status", "konfidenz") if k in m}}
        if q.get("quellenangabe"):
            prov["quellenangabe"] = q["quellenangabe"]
        S.set_controlled(seed, F_FACHGEBIET, fachgebiete, prov)
        faecher.setzen(seed)  # ccm:taxonid: Fach des VHS-Fachgebiets, sonst „Weiterbildung“
        pbr = next(p for p in s["programmbereiche"] if p["notation"] == pb)
        S.set_controlled(seed, F_PROGRAMMBEREICH, [{"uri": pbr["uri"], "label": pbr["label"]}],
                         S.provenance(q["name"], uri=q["uri"], license=q["lizenz"], method="aus dem Fachgebiet"))
        if q.get("quellenangabe"):
            seed[S.F_PROVENANCE][F_PROGRAMMBEREICH]["quellenangabe"] = q["quellenangabe"]
        lrt = self.inhaltstypen["quelle"]
        S.set_controlled(seed, S.F_LRT, [{"uri": typ["uri"], "label": typ["label"]}], S.provenance(
            lrt["name"], uri=lrt["uri"], license=lrt["lizenz"],
            method="passend zu Themenart, Zielgruppe und Programmbereich, je Gruppe rotierend"))
        S.set_controlled(seed, S.F_ROLE, [self.roles.ref(role)], S.provenance(
            "OpenEduHub intendedEndUserRole", uri=self.roles.scheme["uri"], license="CC0-1.0",
            method="gewichtet zufällig je Themenart"))
        zs = opt["zielsprachen"].get(haupt)
        seed[S.F_LANGUAGE] = list(dict.fromkeys(opt["sprache"]["werte"] + ([zs] if zs else [])))

        seed[S.F_THEMA] = {"id": topic["id"], "label_de": topic["label_de"], "beschreibung_de": topic.get("definition", ""),
                           "keywords_de": list(dict.fromkeys([topic["label_de"], *topic.get("synonyme", [])[:4],
                                                              *topic.get("keywords", [])[:4],
                                                              *[o["label"] for o in topic.get("oberbegriffe", [])][:2]])),
                           "art": art}
        tq = topic["quelle"]
        seed[S.F_PROVENANCE][S.F_THEMA] = S.provenance(tq["name"], uri=tq["uri"], license=tq["lizenz"],
                                                       method=m.get("methode", "Thema aus der Quelle"))
        if tq.get("quellenangabe"):
            seed[S.F_PROVENANCE][S.F_THEMA]["quellenangabe"] = tq["quellenangabe"]
        seed[S.F_PROVENANCE][S.F_THEMA].update(
            status={"fachgebiet": m.get("status"), "eignung": (m.get("eignung") or {}).get("status")},
            belege={"erfuellt": sorted(self.belege_des_themas(topic))})
        if niveau:
            seed[S.F_NIVEAU] = {k: niveau[k] for k in ("id", "label", "exactMatch") if k in niveau}
            seed[S.F_PROVENANCE][S.F_NIVEAU] = S.provenance(
                self.niveaus["quelle"]["name"], method="aus dem DigComp-Niveau (digcomp_fachgebiete.json)" if dc
                else "passend zu Programmbereich und Fachgebiet, gleichverteilt")
        if dc:
            from . import digcomp as dmod

            seed.update(dmod.metadaten(dc))
            seed[S.F_PROVENANCE][dmod.F_KOMPETENZ] = S.provenance(
                tq["name"], uri=tq["uri"], license=tq["lizenz"],
                method=f"Lernergebnis {dc['id']} (veröffentlicht als {dc['id_original']}); eigene, nicht amtliche "
                       "Übersetzung")
        seed[S.F_MEDIENFORM] = typ["medienform"]
        seed["synth:textsorte"] = typ["textsorte"]
        es = opt["einfache_sprache"]
        if pb in es["programmbereiche"] or rng.random() < es["anteil_sonst"]:
            seed["synth:sprachniveau"] = "einfache Sprache"
        if rng.random() < opt["didaktische_funktion"]["anteil"]:
            seed[S.F_DIDAKTIK] = rng.choice(opt["didaktische_funktion"]["werte"])
        if rng.random() < opt["bearbeitungsdauer_minuten"]["anteil"]:
            seed[S.F_LEARNING_TIME] = rng.choice(opt["bearbeitungsdauer_minuten"]["werte"])
        if rng.random() < opt["veranstaltungsform"]["anteil"]:
            seed["synth:veranstaltungsform"] = rng.choice(opt["veranstaltungsform"]["werte"])
        return seed

    # --- Prompt-Werte ----------------------------------------------------------------------

    def prompt_values(self, seed: dict) -> dict:
        t = seed[S.F_THEMA]
        rolle = seed[S.F_ROLE][0].rsplit("/", 1)[-1]
        sprachen = [SPRACHNAMEN.get(x, x) for x in seed.get(S.F_LANGUAGE, [])]
        zielsprache = sprachen[1] if len(sprachen) > 1 else ""
        return {
            "rolle": "eine erfahrene Kursleiterin bzw. ein erfahrener Kursleiter an einer Volkshochschule",
            "sprache": ", ".join(sprachen),
            "textsorte": seed["synth:textsorte"],
            "inhaltstyp": seed[S.F_LRT + "_DISPLAYNAME"][0],
            "medienform": seed[S.F_MEDIENFORM],
            "thema": t["label_de"],
            "thema_beschreibung": t.get("beschreibung_de", ""),
            "thema_keywords": ", ".join(t.get("keywords_de", [])),
            "fach": seed[F_FACHGEBIET + "_DISPLAYNAME"][0],
            "programmbereich": seed[F_PROGRAMMBEREICH + "_DISPLAYNAME"][0],
            "bildungsstufe": seed[S.F_CONTEXT + "_DISPLAYNAME"][0],
            "niveau": (seed.get(S.F_NIVEAU) or {}).get("label", "ohne Vorgabe"),
            "zielgruppe": {"learner": "erwachsene Kursteilnehmende", "teacher": "Kursleitende in der Erwachsenenbildung"}.get(
                rolle, seed[S.F_ROLE + "_DISPLAYNAME"][0]),
            "zielsprache": zielsprache,
            "einfache_sprache": seed.get("synth:sprachniveau", ""),
            "veranstaltungsform": seed.get("synth:veranstaltungsform", ""),
            "didaktische_funktion": seed.get(S.F_DIDAKTIK, ""),
            "bearbeitungsdauer": seed.get(S.F_LEARNING_TIME, ""),
        }
