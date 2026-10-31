"""Große JSONL-Dateien lesen, ohne sie ganz zu laden: Zeilen zählen, eine Zeile per Nummer holen, durchsuchen.

Materialdateien werden mehrere hundert MB groß und wachsen, während ein Lauf schreibt. Der Index der
Zeilenanfänge wird einmal gebaut und danach nur um das Angehängte ergänzt.
"""

from __future__ import annotations

import csv
import json
import threading
from array import array
from pathlib import Path

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")  # html aus: Modelltexte sicher
_md_doku = MarkdownIt("commonmark", {"html": True}).enable("table")  # eigene Doku im Repo


def markdown(text: str, doku: bool = False) -> str:
    return (_md_doku if doku else _md).render(text or "")


class _Index:
    def __init__(self):
        self.offsets = array("Q")  # Anfang jeder vollständigen, nicht leeren Zeile
        self.gelesen = 0           # bis hierher ausgewertet (immer Anfang einer Zeile)
        self.kopf = b""            # Dateianfang: ändert er sich, wurde die Datei überschrieben


_indizes: dict[str, _Index] = {}
_sperre = threading.Lock()


def _index(path: Path) -> _Index:
    """Index der Zeilenanfänge; liest nur, was seit dem letzten Aufruf angehängt wurde."""
    key = str(Path(path).resolve())
    with _sperre, open(path, "rb") as f:
        idx = _indizes.get(key) or _Index()
        groesse = f.seek(0, 2)
        f.seek(0)
        kopf = f.read(256)
        if groesse < idx.gelesen or not kopf.startswith(idx.kopf[:len(kopf)]):
            idx = _Index()  # überschrieben
        idx.kopf = kopf
        _indizes[key] = idx
        if groesse > idx.gelesen:
            f.seek(idx.gelesen)
            pos = idx.gelesen
            for zeile in f:
                if not zeile.endswith(b"\n"):
                    break  # halbe letzte Zeile: der Lauf schreibt gerade
                if zeile.strip():
                    idx.offsets.append(pos)
                pos += len(zeile)
            idx.gelesen = pos
        return idx


def zeilen(path: Path) -> int:
    """Anzahl vollständiger Datensätze (0, wenn die Datei fehlt)."""
    return len(_index(path).offsets) if Path(path).exists() else 0


def datensatz(path: Path, nr: int) -> dict | None:
    """Datensatz Nr. nr (ab 0)."""
    idx = _index(path)
    if not 0 <= nr < len(idx.offsets):
        return None
    with open(path, "rb") as f:
        f.seek(idx.offsets[nr])
        try:
            return json.loads(f.readline())
        except json.JSONDecodeError:
            return None


def suchen(path: Path, text: str, maximum: int = 10_000) -> list[int]:
    """Nummern der Datensätze, deren Zeile text enthält (Groß-/Kleinschreibung egal), höchstens maximum."""
    nadel = text.casefold().strip()
    if not nadel:
        return []
    roh = nadel.encode("utf-8")
    n = len(_index(path).offsets)
    out, nr = [], -1
    with open(path, "rb") as f:
        for zeile in f:
            if not zeile.strip():
                continue
            nr += 1
            if nr >= n:
                break  # nach dem Index Angehängtes kommt beim nächsten Aufruf dazu
            # schneller Vorfilter auf Bytes, danach genau (Umlaute in Großschreibung)
            if roh in zeile.lower() or (not roh.isascii() and nadel in zeile.decode("utf-8", "replace").casefold()):
                out.append(nr)
                if len(out) >= maximum:
                    break
    return out


def letzter_datensatz(path: Path) -> dict | None:
    n = zeilen(path)
    return datensatz(path, n - 1) if n else None


def tabelle(path: Path, maximum: int = 200) -> tuple[list[str], list[list[str]]] | dict | None:
    """Spalten und Zeilen für die Anzeige einer Vokabular- oder Mapping-Datei; dict bei JSON ohne Liste."""
    path = Path(path)
    if path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as f:
            r = csv.reader(f)
            kopf = next(r, [])
            return kopf, [z for _, z in zip(range(maximum), r)]
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            daten = [json.loads(z) for _, z in zip(range(maximum), f) if z.strip()]
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        daten = (data.get("concepts") or data.get("inhaltstypen") or data.get("niveaus")) if isinstance(data, dict) \
            else data
        if not isinstance(daten, list):
            return data
        daten = daten[:maximum]
    spalten = list(dict.fromkeys(k for d in daten if isinstance(d, dict) for k in d))
    zellen = [[_zelle(d.get(k)) for k in spalten] for d in daten if isinstance(d, dict)]
    return spalten, zellen


def _zelle(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:300]
    return str(v)
