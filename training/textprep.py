"""Text preparation shared by export, training and prediction.

Training and later use must see the same input, so every script calls ``prepare_input``.

Modes:
- ``head``: plain text, cut by the tokenizer at ``max_source_length``.
- ``condensed``: all headings plus the first sentences of each section. A text of one to two A4 pages
  then fits into 512–768 tokens with its whole structure, so keywords do not only come from the beginning.
"""

from __future__ import annotations

import re

TASKS = {
    # prefix, field in the exported dataset
    "title": "title: ",
    "description": "description: ",
    "keywords": "keywords: ",
}

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ0-9„\"(])")


def strip_markdown(line: str) -> str:
    """Removes inline markdown so synthetic markdown looks like extracted plain text."""
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)        # images
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)         # links
    line = re.sub(r"(\*\*|__|\*|_|`)(\S.*?\S|\S)\1", r"\2", line)  # bold, italics, code
    line = line.replace("|", " ")                                  # tables
    return re.sub(r"\s+", " ", line).strip()


def sections(text: str) -> list[tuple[str, list[str]]]:
    """Splits a text into (heading, paragraphs). Text before the first heading gets an empty heading."""
    out: list[tuple[str, list[str]]] = [("", [])]
    para: list[str] = []

    def flush():
        if para:
            out[-1][1].append(" ".join(para))
            para.clear()

    for raw in text.splitlines():
        m = _HEADING.match(raw)
        if m:
            flush()
            out.append((strip_markdown(m.group(1)), []))
            continue
        if re.fullmatch(r"\s*([-*_]\s*){3,}", raw):  # horizontal rule
            flush()
            continue
        is_item = bool(_LIST.match(raw))
        line = strip_markdown(_LIST.sub("", raw))
        if is_item:  # list items stay separate lines
            flush()
        if not line:
            flush()
        else:
            para.append(line)
            if is_item:
                flush()
    flush()
    return [(h, p) for h, p in out if h or p]


def plain_text(text: str) -> str:
    parts = []
    for heading, paras in sections(text):
        if heading:
            parts.append(heading)
        parts.extend(paras)
    return "\n".join(parts)


def condensed_text(text: str, sentences_per_section: int = 2) -> str:
    parts = []
    for heading, paras in sections(text):
        if heading:
            parts.append(heading)
        kept: list[str] = []  # list items count as one sentence each
        for para in paras:
            kept.extend(_SENTENCE_END.split(para)[: sentences_per_section - len(kept)])
            if len(kept) >= sentences_per_section:
                break
        if kept:
            parts.append("\n".join(kept))
    return "\n".join(parts)


def prepare_input(text: str, mode: str = "head") -> str:
    if mode == "head":
        return plain_text(text)
    if mode == "condensed":
        return condensed_text(text)
    raise ValueError(f"unknown input mode {mode!r}")


def target_text(task: str, row: dict) -> str:
    if task == "keywords":
        return ", ".join(row["keywords"])
    return row[task]
