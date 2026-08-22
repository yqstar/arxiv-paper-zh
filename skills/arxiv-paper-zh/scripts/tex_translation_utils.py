#!/usr/bin/env python3
"""Shared helpers for excluding bibliography content from translation work."""

from __future__ import annotations

import re
from pathlib import Path


_BIBLIOGRAPHY_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<name>thebibliography|biblist|references)\}"
    r".*?"
    r"\\end\s*\{(?P=name)\}",
    re.IGNORECASE | re.DOTALL,
)
_BIBLIOGRAPHY_HEADING = re.compile(
    r"\\(?P<level>chapter|section)\*?\s*"
    r"(?:\[[^]]*\]\s*)?"
    r"\{\s*(?:references|bibliography|literature\s+cited)\s*\}"
    r".*?"
    r"(?="
    r"\\(?:chapter|section)\*?\s*(?:\[[^]]*\]\s*)?\{"
    r"|\\end\s*\{document\}"
    r"|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_BIBITEM_BLOCK = re.compile(
    r"^[ \t]*\\bibitem(?:\[[^]]*\])?\s*\{[^}]*\}.*?"
    r"(?="
    r"^[ \t]*\\bibitem(?:\[[^]]*\])?\s*\{"
    r"|^[ \t]*\\end\s*\{thebibliography\}"
    r"|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_BIBLIOGRAPHY_CONTROL = re.compile(
    r"^[ \t]*\\(?:"
    r"addbibresource|bibliography|bibliographystyle|printbibliography"
    r")\b"
    r"|^[ \t]*\\renewcommand\s*\{\\(?:bibname|refname)\}",
    re.IGNORECASE,
)
_BIBLIOGRAPHY_EXACT_NAMES = {
    "bib",
    "biblio",
    "bibliography",
    "bibliographies",
    "ref",
    "refs",
    "reference",
    "references",
}
_BIBLIOGRAPHY_NAME_PARTS = {
    "bib",
    "biblio",
    "bibliography",
    "bibliographies",
    "refs",
    "references",
}


def _blank(match: re.Match[str]) -> str:
    """Mask content without changing line numbers."""
    return re.sub(r"[^\n]", " ", match.group(0))


def mask_bibliography(text: str) -> str:
    """Blank bibliography regions and controls while preserving newlines."""
    text = _BIBLIOGRAPHY_ENVIRONMENT.sub(_blank, text)
    text = _BIBLIOGRAPHY_HEADING.sub(_blank, text)
    text = _BIBITEM_BLOCK.sub(_blank, text)
    return "\n".join(
        "" if _BIBLIOGRAPHY_CONTROL.match(line) else line
        for line in text.split("\n")
    )


def is_bibliography_file(path: Path) -> bool:
    """Recognize common filenames used only for reference lists."""
    stem = path.stem.lower()
    if stem in _BIBLIOGRAPHY_EXACT_NAMES:
        return True
    parts = {part for part in re.split(r"[-_.]+", stem) if part}
    return bool(parts & _BIBLIOGRAPHY_NAME_PARTS)
