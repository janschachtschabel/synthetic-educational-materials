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
)


def schema():
    import pyarrow as pa

    lst = pa.list_(pa.string())
    return pa.schema([
        ("id", pa.string()), ("education_sector", pa.string()), ("language", pa.string()),
        ("title", pa.string()), ("description", pa.string()), ("keywords", lst), ("fulltext", pa.string()),
        ("qa_pairs", pa.list_(pa.struct([("question", pa.string()), ("answer", pa.string()), ("type", pa.string())]))),
        ("educational_context", lst), ("educational_context_uri", lst), ("subject", lst), ("subject_uri", lst),
        ("content_type", lst), ("content_type_uri", lst), ("content_type_check_match", pa.bool_()),
        ("target_group", lst), ("target_group_uri", lst), ("level", pa.string()), ("text_type", pa.string()),
        ("media_form", pa.string()), ("didactic_function", pa.string()), ("duration_minutes", pa.int32()),
        ("topic", pa.string()), ("topic_uri", pa.string()), ("generator_model", pa.string()),
    ])


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


def flatten(r: dict) -> dict:
    sector = r["synth:bildungsbereich"]
    subj_field = SUBJECT_FIELD.get(sector, ("ccm:taxonid", ""))[0]
    thema = r.get("synth:thema") or {}
    topic_uri = thema.get("id") or ""
    # learning-field topics (ESCO occupation|kmk:…) are kept locally only
    open_topic = topic_uri.startswith(OPEN_TOPIC_PREFIXES) and "kmk:" not in topic_uri
    if open_topic and "|" in topic_uri:  # ESCO occupation|skill: the topic is the skill
        topic_uri = (r.get("synth:kompetenz") or {}).get("uri") or topic_uri.split("|")[0]
    niveau = r.get("synth:niveau") or {}
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
        "text_type": r.get("synth:textsorte"),
        "media_form": r.get("synth:medienform"),
        "didactic_function": r.get("synth:didaktische_funktion"),
        "duration_minutes": r.get("synth:bearbeitungsdauer_minuten"),
        "topic": (thema.get("label_de") or "").strip() or None if open_topic else None,
        "topic_uri": topic_uri if open_topic else None,
        "generator_model": (r.get("synth:generierung") or {}).get("modell"),
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
    text = (HERE / "dataset_card.md").read_text(encoding="utf-8")
    values = {
        "name": name, "size_category": size, "n_total": f"{n:,}", "split_table": "\n".join(table),
        "length_table": "\n".join(lengths), "qa_median": f"{int(statistics.median(qa)) if qa else 0}",
        "qa_total": f"{sum(qa):,}", "content_type_match": match, "models": models, "export_date": date.today().isoformat(),
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
