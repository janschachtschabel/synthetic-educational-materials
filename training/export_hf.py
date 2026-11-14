"""Exports generated materials as a flat Hugging Face dataset (Parquet) with dataset card.

    python training/export_hf.py output/materialien_….jsonl --out hf_export
    huggingface-cli upload JanSchachtschabel/synthetic-german-educational-materials-50k hf_export . --repo-type dataset

Only the fields useful for training text models are exported, flattened and with English column names. Left out:
provenance, generation details, seed parameters and topic labels from sources that are kept locally only
(KMK learning fields, educational standards outlines, adult-education curricula); see METHODOLOGY.md.

Splits are assigned by topic (hash of the topic URI), so materials on the same topic never end up in both training
and test data. Default 90/5/5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUBJECT_FIELD = {
    "hochschule": ("ccm:oeh_taxonid_university", "Destatis higher education subject classification (OEH)"),
    "schule": ("ccm:taxonid", "OEH discipline"),
    "elementar": ("ccm:taxonid", "OEH discipline (closeMatch)"),
    "berufsbildung": ("ccm:oeh_profession_group", "KldB 2010 (OEH)"),
    "erwachsenenbildung": ("synth:fachgebiet_vhs", "DIE adult education centre statistics, subject areas"),
}
SECTOR_EN = {
    "hochschule": "higher_education",
    "schule": "school",
    "elementar": "early_childhood",
    "berufsbildung": "vocational",
    "erwachsenenbildung": "adult_education",
}
# Topic labels are exported only from these open sources (others are kept locally, see METHODOLOGY.md)
OPEN_TOPIC_PREFIXES = (
    "https://d-nb.info/gnd/",
    "http://w3id.org/openeduhub/vocabs/oeh-topics/",
    "http://data.europa.eu/esco/",
    "https://openalex.org/",
    "http://rvk.uni-regensburg.de/nt/",  # Regensburger Verbundklassifikation (CC0)
    "https://publications.jrc.ec.europa.eu/repository/handle/JRC144121#",  # DigComp 3.0 learning outcomes (CC BY 4.0)
)


# Topics abstracted by the project from WirLernenOnline records (python -m sem wlo-themen): label only, no URI
OWN_TOPIC_PREFIXES = ("wlo-abstraktion:",)


# Export columns and their types (str, list of str, bool, int, qa); schema() turns them into a pyarrow schema
COLUMNS = [
    ("id", "str"), ("education_sector", "str"), ("language", "str"), ("title", "str"), ("description", "str"),
    ("keywords", "list"), ("fulltext", "str"), ("qa_pairs", "qa"),
    ("educational_context", "list"), ("educational_context_uri", "list"), ("subject", "list"),
    ("subject_uri", "list"), ("content_type", "list"), ("content_type_uri", "list"),
    ("content_type_check_match", "bool"), ("target_group", "list"), ("target_group_uri", "list"), ("level", "str"),
    ("level_uri", "str"), ("discipline", "list"), ("discipline_uri", "list"), ("curriculum", "list"),
    ("curriculum_uri", "list"), ("occupation", "str"), ("occupation_uri", "str"), ("text_type", "str"),
    ("media_form", "str"), ("didactic_function", "str"), ("duration_minutes", "int"), ("topic", "str"),
    ("topic_uri", "str"), ("cognitive_level", "str"), ("social_form", "str"), ("special_needs", "str"),
    ("teaching_level", "str"), ("early_childhood_area", "list"), ("programme_area", "list"), ("event_format", "str"),
    ("plain_language", "bool"), ("competence", "str"), ("competence_uri", "str"), ("learning_venue", "str"),
    ("cross_cutting_topic", "str"), ("generator_model", "str"), ("title_previous", "str"),
    ("description_previous", "str"), ("rvk_notation", "str"), ("digcomp_area", "str"), ("digcomp_competence", "str"),
    ("digcomp_competence_id", "str"), ("digcomp_level", "str"), ("digcomp_knowledge_type", "str"),
    ("digcomp_ai", "str"),
]


def schema():
    import pyarrow as pa

    types = {"str": pa.string(), "list": pa.list_(pa.string()), "bool": pa.bool_(), "int": pa.int32(),
             "qa": pa.list_(pa.struct([("question", pa.string()), ("answer", pa.string()), ("type", pa.string())]))}
    return pa.schema([(name, types[t]) for name, t in COLUMNS])


def _uris(r: dict, field: str) -> list[str]:
    out = []
    for u in r.get(field) or []:
        if "programmbereiche.json#" in u:  # VHS subject areas: repository-internal URI → code only
            u = "die-vhs:" + u.rsplit("#", 1)[1]
        out.append(u)
    return out


def _labels(r: dict, field: str) -> list[str]:
    return list(r.get(field + "_DISPLAYNAME") or [])


def topic_key(r: dict) -> str:
    t = r.get("synth:thema") or {}
    return t.get("id") or (r.get("synth:seed_parameter") or {}).get("gruppe") or r["id"]


def split_of(key: str, val: int, test: int) -> str:
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "test" if h < test else "validation" if h < test + val else "train"


# Seed fields and where they go in the export. Every field a seed can carry must be listed in one of the two maps
# (tests/test_export_hf.py checks this against generated seeds), so that no classifying metadata gets lost unnoticed.
EXPORTED_SEED_FIELDS = {
    "id": "id", "synth:bildungsbereich": "education_sector", "cclom:general_language": "language",
    "ccm:educationalcontext": "educational_context(_uri)", "ccm:oeh_taxonid_university": "subject(_uri)",
    "ccm:oeh_profession_group": "subject(_uri)", "synth:fachgebiet_vhs": "subject(_uri)",
    "ccm:taxonid": "discipline(_uri), school/early childhood also subject(_uri)",
    "ccm:curriculum": "curriculum(_uri)", "ccm:oeh_lrt": "content_type(_uri)",
    "ccm:educationalintendedenduserrole": "target_group(_uri)", "synth:niveau": "level, level_uri",
    "synth:thema": "topic, topic_uri, teaching_level", "synth:beruf": "occupation(_uri)",
    "synth:kompetenz": "competence(_uri)", "synth:textsorte": "text_type", "synth:medienform": "media_form",
    "synth:didaktische_funktion": "didactic_function", "synth:bearbeitungsdauer_minuten": "duration_minutes",
    "synth:anforderungsbereich": "cognitive_level", "synth:sozialform": "social_form",
    "synth:foerderschwerpunkt": "special_needs", "synth:bildungsbereich_elementar": "early_childhood_area",
    "synth:programmbereich_vhs": "programme_area", "synth:veranstaltungsform": "event_format",
    "synth:sprachniveau": "plain_language", "synth:lernort": "learning_venue",
    "synth:querschnittsthema": "cross_cutting_topic",
    "synth:digcomp_bereich": "digcomp_area", "synth:digcomp_kompetenz": "digcomp_competence(_id)",
    "synth:digcomp_niveau": "digcomp_level", "synth:digcomp_art": "digcomp_knowledge_type", "synth:digcomp_ki": "digcomp_ai",
}
NOT_EXPORTED_SEED_FIELDS = {
    "synth:provenance": "generation details, documented in METHODOLOGY.md",
    "synth:seed_parameter": "generation details",
    "synth:umfang_woerter": "generation control (target length), not a property of the material",
    "synth:stil": "generation control (opening, names), not a property of the material",
    "synth:bildungsstandards": "KMK educational standards outline, kept locally only (not openly licensed)",
    "synth:lernfeld": "KMK framework curricula, kept locally only (not openly licensed)",
}


def flatten(r: dict) -> dict:
    sector = r["synth:bildungsbereich"]
    subj_field = SUBJECT_FIELD.get(sector, ("ccm:taxonid", ""))[0]
    thema = r.get("synth:thema") or {}
    topic_uri = thema.get("id") or ""
    # learning-field topics (ESCO occupation|kmk:…) are kept locally only
    open_topic = topic_uri.startswith(OPEN_TOPIC_PREFIXES) and "kmk:" not in topic_uri
    # topics of the project's own making (CC0), label only: subject profiles and topics abstracted from WirLernenOnline
    # records (no source text, see PROVENIENZ.md)
    profile_topic = "#thema-" in topic_uri or topic_uri.startswith(OWN_TOPIC_PREFIXES)
    if open_topic and "|" in topic_uri:  # ESCO occupation|skill: the topic is the skill
        topic_uri = (r.get("synth:kompetenz") or {}).get("uri") or topic_uri.split("|")[0]
    niveau = r.get("synth:niveau") or {}
    komp = r.get("synth:kompetenz") or {}
    check = r.get("synth:inhaltstyp_pruefung") or {}
    return {
        "id": r["id"],
        "education_sector": SECTOR_EN.get(sector, sector),
        "language": (r.get("cclom:general_language") or ["de"])[0],
        "title": r["cclom:title"],
        "description": r["cclom:general_description"],
        "keywords": list(r["cclom:general_keyword"]),
        "fulltext": r["synth:volltext"],
        "qa_pairs": [{"question": p.get("frage", ""), "answer": p.get("antwort", ""), "type": p.get("typ", "")}
                     for p in r.get("synth:frage_antwort_paare") or []],
        "educational_context": _labels(r, "ccm:educationalcontext"),
        "educational_context_uri": _uris(r, "ccm:educationalcontext"),
        "subject": _labels(r, subj_field),
        "subject_uri": _uris(r, subj_field),
        "content_type": _labels(r, "ccm:oeh_lrt"),
        "content_type_uri": _uris(r, "ccm:oeh_lrt"),
        "content_type_check_match": check.get("uebereinstimmung"),
        "target_group": _labels(r, "ccm:educationalintendedenduserrole"),
        "target_group_uri": _uris(r, "ccm:educationalintendedenduserrole"),
        "level": niveau.get("label"),
        # KIM educationalLevel or OEH dqrCompetenceLevels; empty where the level has no vocabulary value
        "level_uri": niveau.get("exactMatch"),
        # OEH discipline (ccm:taxonid) in every sector that has it; higher education uses `subject` instead
        "discipline": _labels(r, "ccm:taxonid"),
        "discipline_uri": _uris(r, "ccm:taxonid"),
        # WirLernenOnline topic tree (school only, ccm:curriculum)
        "curriculum": _labels(r, "ccm:curriculum"),
        "curriculum_uri": _uris(r, "ccm:curriculum"),
        "occupation": (r.get("synth:beruf") or {}).get("label"),
        "occupation_uri": (r.get("synth:beruf") or {}).get("uri"),
        "text_type": r.get("synth:textsorte"),
        "media_form": r.get("synth:medienform"),
        "didactic_function": r.get("synth:didaktische_funktion"),
        "duration_minutes": r.get("synth:bearbeitungsdauer_minuten"),
        "topic": (thema.get("label_de") or "").strip() or None if open_topic or profile_topic else None,
        "topic_uri": topic_uri if open_topic else None,
        "cognitive_level": r.get("synth:anforderungsbereich"),
        "social_form": r.get("synth:sozialform"),
        "special_needs": r.get("synth:foerderschwerpunkt"),
        "teaching_level": thema.get("lehrebene"),
        "early_childhood_area": _labels(r, "synth:bildungsbereich_elementar"),
        "programme_area": _labels(r, "synth:programmbereich_vhs"),
        "event_format": r.get("synth:veranstaltungsform"),
        "plain_language": r.get("synth:sprachniveau") == "einfache Sprache",
        "competence": komp.get("label"),
        "competence_uri": komp.get("uri"),
        "learning_venue": r.get("synth:lernort"),
        "cross_cutting_topic": r.get("synth:querschnittsthema"),
        "generator_model": (r.get("synth:generierung") or {}).get("modell"),
        # set when the title was regenerated afterwards (python -m sem titel-neu): the title it replaced
        "title_previous": r.get("synth:titel_alt"),
        # set when the first sentence of the description was rewritten (python -m sem beschreibung-einstieg)
        "description_previous": r.get("synth:beschreibung_alt"),
        # RVK class of the topic (higher education, topic source RVK)
        "rvk_notation": (thema.get("rvk") or {}).get("notation"),
        # DigComp 3.0 (adult education, optionally vocational): area, competence, level (Basic … Highly advanced),
        # knowledge/skill/attitude and AI relation (explicit, implicit, none); German labels are the project's own
        # translation
        "digcomp_area": (r.get("synth:digcomp_bereich") or {}).get("label"),
        "digcomp_competence": (r.get("synth:digcomp_kompetenz") or {}).get("label"),
        "digcomp_competence_id": (r.get("synth:digcomp_kompetenz") or {}).get("id"),
        "digcomp_level": r.get("synth:digcomp_niveau"),
        "digcomp_knowledge_type": {"Wissen": "knowledge", "Fähigkeit": "skill", "Haltung": "attitude"}.get(
            r.get("synth:digcomp_art"), r.get("synth:digcomp_art")),
        "digcomp_ai": {"explizit": "explicit", "implizit": "implicit", "ohne": "none"}.get(
            r.get("synth:digcomp_ki"), r.get("synth:digcomp_ki")),
    }


def _stats(values: list[int]) -> str:
    if not values:
        return "-"
    s = sorted(values)
    return f"{int(statistics.median(s)):,} / {s[int(0.95 * (len(s) - 1))]:,}"


def build_card(rows_by_split: dict[str, list[dict]], name: str) -> str:
    rows = [r for rs in rows_by_split.values() for r in rs]
    n = len(rows)
    sectors = Counter(r["education_sector"] for r in rows)
    per_split = {s: Counter(r["education_sector"] for r in rs) for s, rs in rows_by_split.items()}
    table = ["| Sector | train | validation | test | total |", "|---|---:|---:|---:|---:|"]
    for sec, cnt in sectors.most_common():
        table.append(f"| {sec} | " + " | ".join(f"{per_split.get(s, {}).get(sec, 0):,}"
                                                 for s in ("train", "validation", "test")) + f" | {cnt:,} |")
    table.append("| **total** | " + " | ".join(f"{len(rows_by_split.get(s, [])):,}"
                                              for s in ("train", "validation", "test")) + f" | {n:,} |")
    lengths = ["| Field | median / 95th percentile (characters) |", "|---|---|"]
    for field, fn in (("fulltext", len), ("title", len), ("description", len),
                      ("keywords (joined)", None), ("keywords (count)", None)):
        if field == "keywords (joined)":
            vals = [len(", ".join(r["keywords"])) for r in rows]
        elif field == "keywords (count)":
            vals = [len(r["keywords"]) for r in rows]
        else:
            vals = [fn(r[field]) for r in rows]
        lengths.append(f"| {field} | {_stats(vals)} |")
    qa = [len(r["qa_pairs"]) for r in rows]
    checked = [r["content_type_check_match"] for r in rows if r["content_type_check_match"] is not None]
    match = f"{100 * sum(checked) / len(checked):.1f} %" if checked else "-"
    models = ", ".join(sorted({r["generator_model"] for r in rows if r["generator_model"]})) or "-"
    size = "10K<n<100K" if n < 100_000 else "100K<n<1M"
    regenerated = [r for r in rows if r.get("title_previous") is not None]
    replaced = sum(r["title"] != r["title_previous"] for r in regenerated)
    title_note = ("" if not regenerated else
                  f" Titles were regenerated afterwards with a revised title prompt (readable phrase instead of a keyword "
                  f"chain; {replaced:,} of {len(regenerated):,} titles changed); the earlier titles are kept in "
                  f"`title_previous`.")
    rewritten = [r for r in rows if r.get("description_previous") is not None]
    if rewritten:
        title_note += (f" In {len(rewritten):,} descriptions whose first sentence was a stock phrase (e.g. „Im Mittelpunkt "
                       f"steht …“, „Das Material behandelt …“), only that sentence was rewritten afterwards; the rest is "
                       f"unchanged and the earlier description is kept in `description_previous`.")
    text = (HERE / "dataset_card.md").read_text(encoding="utf-8")
    values = {
        "name": name, "size_category": size, "n_total": f"{n:,}", "split_table": "\n".join(table),
        "length_table": "\n".join(lengths), "qa_median": f"{int(statistics.median(qa)) if qa else 0}",
        "qa_total": f"{sum(qa):,}", "content_type_match": match, "models": models, "export_date": date.today().isoformat(),
        "title_note": title_note,
    }
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("materials", nargs="+", type=Path, help="materials JSONL file(s) from python -m sem")
    ap.add_argument("--out", type=Path, default=Path("hf_export"))
    ap.add_argument("--name", default="synthetic-german-educational-materials-50k", help="dataset name in the card")
    ap.add_argument("--val", type=int, default=5, help="validation share in percent")
    ap.add_argument("--test", type=int, default=5, help="test share in percent")
    a = ap.parse_args(argv)

    import pyarrow as pa
    import pyarrow.parquet as pq

    rows_by_split: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    skipped = 0
    for path in a.materials:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r["id"] in seen or not r.get("synth:volltext") or not r.get("cclom:title"):
                    skipped += 1
                    continue
                seen.add(r["id"])
                rows_by_split[split_of(topic_key(r), a.val, a.test)].append(flatten(r))

    data_dir = a.out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in rows_by_split.items():
        pq.write_table(pa.Table.from_pylist(rows, schema=schema()), data_dir / f"{split}-00000-of-00001.parquet", compression="zstd")
    (a.out / "README.md").write_text(build_card(rows_by_split, a.name), encoding="utf-8")
    print(f"{len(seen):,} materials exported to {a.out} "
          + ", ".join(f"{s}: {len(r):,}" for s, r in sorted(rows_by_split.items()))
          + (f"; skipped {skipped} (duplicate or incomplete)" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
