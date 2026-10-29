"""Editierbare Prompt-Vorlagen.

Platzhalter: {{name}}. Bedingte Abschnitte: {{#name}}…{{/name}} erscheinen nur, wenn name einen
Wert hat. Bereichsspezifische Fassungen unter prompts/<bereich>/ haben Vorrang.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .paths import PROMPT_DIR

# volltext_system + volltext erzeugen den Text; system + material sind für alle Metadatenfelder gleich,
# die Felddateien liefern den jeweiligen Auftrag
PROMPT_FILES = ["volltext_system", "volltext", "system", "material", "titel", "beschreibung", "keywords",
                "inhaltstyp", "fragen_antworten"]
FELD_PROMPTS = ["titel", "beschreibung", "keywords", "inhaltstyp", "fragen_antworten"]

_SECTION = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")


def render(template: str, values: dict) -> str:
    def section(m):
        return m.group(2) if values.get(m.group(1)) else ""

    text = _SECTION.sub(section, template)
    return _VAR.sub(lambda m: str(values.get(m.group(1), "")), text)


def placeholders(template: str) -> list[str]:
    return sorted(set(_VAR.findall(template)) | {m.group(1) for m in _SECTION.finditer(template)})


def prompt_path(name: str, bereich: str | None = None, base: Path = PROMPT_DIR) -> Path:
    if bereich and (base / bereich / f"{name}.md").exists():
        return base / bereich / f"{name}.md"
    return base / f"{name}.md"


def load_prompts(bereich: str | None = None, base: Path = PROMPT_DIR) -> dict[str, str]:
    return {n: prompt_path(n, bereich, base).read_text(encoding="utf-8").strip() for n in PROMPT_FILES}


def prompt_hash(prompts: dict[str, str]) -> str:
    h = hashlib.sha256()
    for n in sorted(prompts):
        h.update(n.encode() + b"\0" + prompts[n].encode() + b"\0")
    return h.hexdigest()[:12]
