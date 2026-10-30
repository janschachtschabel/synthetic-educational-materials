import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
VOCAB_DIR = DATA_DIR / "vocabs"
MAPPING_DIR = DATA_DIR / "mappings"
# Nur lokal (nicht im Repo): vollständige Gliederungen urheberrechtlich geschützter Vorlagen und die daraus
# gebauten Themen. Alles läuft auch ohne diese Dateien, dann ohne die Teile, die von ihnen abhängen.
LOKAL_DIR = DATA_DIR / "lokal"
# Datei unter LOKAL_DIR → Bildungsbereich, dessen Seeds von ihr abhängen
LOKALE_DATEIEN = {
    "schule/bildungsstandards_gliederung.json": "schule",
    "berufsbildung/rahmenlehrplaene.jsonl": "berufsbildung",
    "erwachsenenbildung/themen_lokal.jsonl": "erwachsenenbildung",
}


def lokal_pfad(name: str) -> Path:
    """Pfad einer lokalen Datei. Liest LOKAL_DIR beim Aufruf, damit Tests den Ordner umlenken können."""
    return LOKAL_DIR / name


def lokale_daten(bereich: str | None = None) -> dict[str, str | None]:
    """Kennung der lokalen Dateien (sha256, 12 Zeichen; None = fehlt), optional nur die eines Bereichs.
    Steht in Seeds und Statistik: Gleicher Startwert ergibt nur bei gleicher Kennung dieselben Seeds."""
    out = {}
    for name, b in LOKALE_DATEIEN.items():
        if bereich in (None, b):
            p = lokal_pfad(name)
            out[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else None
    return out
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
