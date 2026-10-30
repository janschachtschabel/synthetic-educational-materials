"""Listet Paare deutscher und englischer Dokumentation, bei denen die deutsche Fassung neuer ist.

    python scripts/doku_paare.py          # nur veraltete englische Fassungen
    python scripts/doku_paare.py --alle   # alle Paare mit Stand

Paare: <name>.de.md ↔ <name>.md sowie METHODIK.md ↔ METHODOLOGY.md. Stand ist der letzte Commit, der die
Datei geändert hat; hat die Datei nicht committete Änderungen, zählt ihr Änderungszeitpunkt auf der Platte.
Rückgabewert 1, wenn eine englische Fassung veraltet ist (für CI oder einen Pre-Commit-Hook).
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FESTE_PAARE = [("METHODIK.md", "METHODOLOGY.md")]


def paare() -> list[tuple[Path, Path]]:
    out = [(ROOT / de, ROOT / en) for de, en in FESTE_PAARE]
    for de in sorted(ROOT.rglob("*.de.md")):
        if ".git" not in de.parts:
            out.append((de, de.with_name(de.name.removesuffix(".de.md") + ".md")))
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def stand(path: Path) -> datetime | None:
    if not path.exists():
        return None
    rel = path.relative_to(ROOT).as_posix()
    if _git("status", "--porcelain", "--", rel):  # geändert oder neu: Zeitpunkt auf der Platte
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    iso = _git("log", "-1", "--format=%cI", "--", rel)
    return datetime.fromisoformat(iso) if iso else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows-Konsole: sonst cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    alle = "--alle" in argv
    veraltet = 0
    for de, en in paare():
        sd, se = stand(de), stand(en)
        if se is None:
            zustand = "englische Fassung fehlt"
        elif sd is not None and sd > se:
            zustand = "englische Fassung veraltet"
        else:
            zustand = "aktuell"
        veraltet += zustand != "aktuell"
        if alle or zustand != "aktuell":
            fmt = lambda d: d.astimezone().strftime("%Y-%m-%d %H:%M") if d else "-"  # noqa: E731
            print(f"{zustand:26} {de.relative_to(ROOT).as_posix()} ({fmt(sd)}) → {en.relative_to(ROOT).as_posix()} ({fmt(se)})")
    if not veraltet and not alle:
        print("Alle englischen Fassungen sind mindestens so neu wie die deutschen.")
    return 1 if veraltet else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
