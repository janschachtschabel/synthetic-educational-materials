from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
VOCAB_DIR = DATA_DIR / "vocabs"
MAPPING_DIR = DATA_DIR / "mappings"
# Nur lokal (nicht im Repo): vollständige Gliederungen urheberrechtlich geschützter Vorlagen als Build-Eingabe
LOKAL_DIR = DATA_DIR / "lokal"
PROMPT_DIR = ROOT / "prompts"
OUTPUT_DIR = ROOT / "output"

# Feste Namensanfänge in output/: daran erkennen Kommandozeile und Oberfläche die Dateiart
PRAEFIX = {"seeds": "seeds_", "materialien": "materialien_"}


def ausgabe_pfad(art: str, name: str | Path, ordner: Path = OUTPUT_DIR) -> Path:
    """Pfad mit festem Präfix und .jsonl: („seeds“, „test“) → output/seeds_test.jsonl. Ein anderes
    Präfix im Namen wird ersetzt; ein mitgegebener Ordner bleibt erhalten."""
    p = Path(name)
    stamm = p.name.strip().removesuffix(".jsonl")
    for q in PRAEFIX.values():
        stamm = stamm.removeprefix(q)
    ziel = p.parent if str(p.parent) not in ("", ".") else ordner
    return ziel / f"{PRAEFIX[art]}{stamm or 'ohne_namen'}.jsonl"


def ausgabedateien(art: str, ordner: Path = OUTPUT_DIR) -> list[Path]:
    """Dateien einer Art in output/, neueste zuerst (ohne Fehlerprotokolle)."""
    ps = [p for p in ordner.glob(f"{PRAEFIX[art]}*.jsonl") if not p.name.endswith(".fehler.jsonl")]
    return sorted(ps, key=lambda p: p.stat().st_mtime, reverse=True)
