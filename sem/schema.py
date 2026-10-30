"""Einheitliche Datenstruktur für Seeds und Materialien.

Feldnamen folgen dem edu-sharing/OpenEduHub-Metadatenschema (Präfixe ``cclom:`` und ``ccm:``).
Kontrollierte Felder stehen wie dort als Liste von URIs, das zugehörige Label im Feld
``<feld>_DISPLAYNAME``. Felder ohne Entsprechung im OEH-Schema tragen das Präfix ``synth:``.
Zu jedem kontrollierten Feld steht unter ``synth:provenance`` die Herkunft.
"""

from __future__ import annotations

# OEH-Felder, die Seeds und Materialien verwenden
F_CONTEXT = "ccm:educationalcontext"               # Bildungsstufe (OEH educationalContext)
F_TAXON = "ccm:taxonid"                            # Schulfach (OEH discipline)
F_TAXON_UNI = "ccm:oeh_taxonid_university"         # Hochschulfach (OEH hochschulfaechersystematik)
F_LRT = "ccm:oeh_lrt"                              # Inhaltstyp (OEH new_lrt)
F_ROLE = "ccm:educationalintendedenduserrole"      # Zielgruppe (OEH intendedEndUserRole)
F_PROFESSION = "ccm:oeh_profession_group"          # Berufsgruppe (OEH kldb, Klassifikation der Berufe)
F_CURRICULUM = "ccm:curriculum"                    # Thema im WLO-Themenbaum (OEH oehTopics)
F_LANGUAGE = "cclom:general_language"
F_TITLE = "cclom:title"
F_DESCRIPTION = "cclom:general_description"
F_KEYWORD = "cclom:general_keyword"

# Eigene Felder
F_BEREICH = "synth:bildungsbereich"
F_THEMA = "synth:thema"
F_NIVEAU = "synth:niveau"
F_DIDAKTIK = "synth:didaktische_funktion"
F_MEDIENFORM = "synth:medienform"
F_BERUF = "synth:beruf"                              # ESCO-Beruf (Berufsbildung)
F_KOMPETENZ = "synth:kompetenz"                      # ESCO-Kompetenz, die am Beruf hängt (Berufsbildung)
F_LERNORT = "synth:lernort"
F_QUERSCHNITT = "synth:querschnittsthema"
F_LEARNING_TIME = "synth:bearbeitungsdauer_minuten"  # OEH cclom:typicallearningtime zählt Millisekunden
F_FULLTEXT = "synth:volltext"
F_QA = "synth:frage_antwort_paare"
F_LRT_CHECK = "synth:inhaltstyp_pruefung"           # Inhaltstyp aus dem fertigen Material bestimmt
F_PROVENANCE = "synth:provenance"
F_GENERATION = "synth:generierung"


def set_controlled(record: dict, field: str, refs: list[dict], provenance: dict) -> None:
    """Setzt ein kontrolliertes Feld im OEH-Stil: URIs, DISPLAYNAME und Herkunft."""
    record[field] = [r["uri"] for r in refs]
    record[field + "_DISPLAYNAME"] = [r["label"] for r in refs]
    record.setdefault(F_PROVENANCE, {})[field] = provenance


def provenance(source: str, *, uri: str | None = None, license: str | None = None, method: str) -> dict:
    """Herkunftsangabe für ein Feld.

    source: Name der Quelle (z. B. Vokabular), uri: Quelle als URL, method: wie der Wert entstand
    (z. B. "fest codiert", "aus Thema abgeleitet", "zufällig gleichverteilt", "LLM").
    """
    p = {"quelle": source, "methode": method}
    if uri:
        p["uri"] = uri
    if license:
        p["lizenz"] = license
    return p
