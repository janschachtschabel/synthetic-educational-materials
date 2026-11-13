"""DigComp 3.0 (European Digital Competence Framework, Fifth Edition): Data Supplement lesen, Errata anwenden,
eigene deutsche Übersetzung ablegen.

Quelle: JRC Data Supplement (JSON-LD, Stand 24.11.2025),
https://jeodpp.jrc.ec.europa.eu/ftp/public/JRC-OpenData/DIGCOMP/DIGCOMP3.0/ ; © European Union, 2025, CC BY 4.0.
Die Rohdatei bleibt lokal (data/lokal/digcomp/); ins Repo kommt data/mappings/digcomp/digcomp30.jsonl: fünf Bereiche,
21 Kompetenzen und die Lernergebnisse nach den Errata vom 27.01.2026 (522 statt 523), englisch wörtlich und mit
eigener, nicht amtlicher deutscher Übersetzung.

Errata (https://joint-research-centre.ec.europa.eu/scientific-activities/key-competences-lifelong-learning/
digital-competence-framework-digcomp/digcomp-30-resources/digcomp-30-errata_en): Das JRC hat weder PDF noch
Data Supplement korrigiert; adaptierte Fassungen sollen die Errata einarbeiten. LO2.5.09 doppelt LO2.5.07 und
entfällt, LO2.5.10 bis LO2.5.21 rücken auf LO2.5.09 bis LO2.5.20; sechs Lernergebnisse bekommen den korrigierten
Wortlaut. Der veröffentlichte Wortlaut bleibt in text_original, die veröffentlichte ID in id_original.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..paths import MAPPING_DIR, lokal_pfad

DIR = MAPPING_DIR / "digcomp"
DATEI = DIR / "digcomp30.jsonl"
GLOSSAR = DIR / "glossar.json"
QUELLE = {
    "name": "DigComp 3.0: European Digital Competence Framework (JRC144121)",
    "uri": "https://publications.jrc.ec.europa.eu/repository/handle/JRC144121",
    "lizenz": "CC-BY-4.0",
    "lizenz_uri": "https://creativecommons.org/licenses/by/4.0/",
    "daten": "https://jeodpp.jrc.ec.europa.eu/ftp/public/JRC-OpenData/DIGCOMP/DIGCOMP3.0/",
    "quellenangabe": "DigComp 3.0: European Digital Competence Framework – Fifth Edition (Cosgrove, J. und Cachia, R., "
                     "Publications Office of the European Union, 2025), © European Union, 2025, CC BY 4.0, JRC144121, "
                     "https://data.europa.eu/doi/10.2760/0001149; Data Supplement vom 24.11.2025. Verändert: Errata "
                     "vom 27.01.2026 angewendet (522 Lernergebnisse), ins Deutsche übersetzt (eigene, nicht amtliche "
                     "Übersetzung) und für Seeds gekürzt. Die Europäische Kommission ist für diese Fassung nicht "
                     "verantwortlich.",
}
URI_BASIS = QUELLE["uri"] + "#"
UEBERSETZUNG_HINWEIS = "eigene, nicht amtliche Übersetzung"
ERRATA_URL = ("https://joint-research-centre.ec.europa.eu/scientific-activities/key-competences-lifelong-learning/"
              "digital-competence-framework-digcomp/digcomp-30-resources/digcomp-30-errata_en")
ERRATA_STAND = "2026-01-27"
# Datenrelevante Errata (wörtlich aus der Errata-Liste)
ERRATA_LOESCHEN = {"LO2.5.09": "duplicates LO2.5.07"}
ERRATA_TEXT = {
    "LO2.5.15": ("respective", "respectful"),  # nur ein Wort
    "LO2.6.27": "Stay informed about developments in digital technologies in relation to digital identity management "
                "and protection",
    "LO4.3.18": "Describe strategies to help protect against and respond effectively to harmful behaviour, content and "
                "deceptive design in digital environments",
    "LO4.3.22": "Implement strategies to help protect oneself against and respond effectively to harmful behaviour, "
                "content and deceptive design in digital environments",
    "LO5.2.12": "Adjust features of digital environments, and use digital assistance tools and assistive technologies, "
                "to suit one’s own and others’ needs and preferences",
    "LO5.4.13": "Support others to develop confidence, autonomy and problem-solving capabilities in digital environments",
}
# Feste deutsche Bezeichnungen (eigene Übersetzung)
NIVEAUS = {"Basic": "Grundstufe", "Intermediate": "Mittelstufe", "Advanced": "Fortgeschritten",
           "Highly advanced": "Hoch fortgeschritten"}
ARTEN = {"Knowledge": "Wissen", "Skill": "Fähigkeit", "Attitude": "Haltung"}
KI = {"AI-Explicit": "explizit", "AI-Implicit": "implizit", "AI not Implicit or Explicit": "ohne"}


def standard_datei() -> Path | None:
    ps = sorted(lokal_pfad("digcomp").glob("*.jsonld"))
    return ps[-1] if ps else None


def _kurz(x: str) -> str:
    return x.split("/", 1)[1] if "/" in x else x


def lese_jsonld(pfad: str | Path) -> dict:
    """Bereiche, Kompetenzen und Lernergebnisse aus dem Data Supplement, Errata angewendet."""
    g = json.loads(Path(pfad).read_text(encoding="utf-8"))["@graph"]
    bereiche = [{"typ": "bereich", "id": _kurz(x["@id"]), "label_en": x["name"], "beschreibung_en": x["description"]}
                for x in g if x["@type"] == "CompetenceArea"]
    kompetenzen = [{"typ": "kompetenz", "id": _kurz(x["@id"]), "bereich": _kurz(x["competence_area_id"]),
                    "label_en": x["name"], "beschreibung_en": x["description"]}
                   for x in g if x["@type"] == "Competence"]
    los = []
    for x in g:
        if x["@type"] != "LearningOutcome":
            continue
        oid = _kurz(x["@id"])
        if oid in ERRATA_LOESCHEN:
            continue
        text = x["description"].strip()
        lo = {"typ": "lernergebnis", "id": oid, "id_original": oid, "kompetenz": _kurz(x["competence_id"]),
              "bereich": _kurz(x["competence_area_id"]), "niveau": _kurz(x["four_levels_proficiency_name"]),
              "art": x.get("type"), "ki": x.get("ai_label"), "text_en": text}
        korr = ERRATA_TEXT.get(oid)
        if korr:
            neu = text.replace(*korr) if isinstance(korr, tuple) else korr
            if neu != text:
                lo["text_original"], lo["text_en"] = text, neu
                lo["erratum"] = ERRATA_STAND
        los.append(lo)
    # Neunummerierung nach dem gelöschten Duplikat: LO2.5.10 … → LO2.5.09 …
    for geloescht in ERRATA_LOESCHEN:
        m = re.fullmatch(r"(LO\d\.\d\.)(\d\d)", geloescht)
        praefix, nr = m.group(1), int(m.group(2))
        for lo in los:
            n = re.fullmatch(re.escape(praefix) + r"(\d\d)", lo["id_original"])
            if n and int(n.group(1)) > nr:
                lo["id"] = f"{praefix}{int(n.group(1)) - 1:02d}"
                lo["erratum"] = ERRATA_STAND
    return {"bereiche": bereiche, "kompetenzen": kompetenzen, "lernergebnisse": los,
            "glossar": [{"en": _kurz(x["@id"]), "erklaerung_en": x["explanation"]} for x in g if x["@type"] == "Glossary"]}


def uri(lo_id: str) -> str:
    return URI_BASIS + lo_id


def laden(pfad: Path = DATEI) -> list[dict]:
    if not pfad.exists():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()]


def lernergebnisse(nur_uebersetzt: bool = True) -> list[dict]:
    """Lernergebnisse mit Bereich und Kompetenz (je {"id", "label_en", "label_de", …}); nur_uebersetzt: nur solche
    mit deutschem Text (die Seeds sind deutsch)."""
    rows = laden()
    ber = {r["id"]: r for r in rows if r["typ"] == "bereich"}
    kom = {r["id"]: r for r in rows if r["typ"] == "kompetenz"}
    out = []
    for r in rows:
        if r["typ"] != "lernergebnis" or (nur_uebersetzt and not r.get("text_de")):
            continue
        out.append({**r, "bereich_obj": ber.get(r["bereich"], {}), "kompetenz_obj": kom.get(r["kompetenz"], {})})
    return out
