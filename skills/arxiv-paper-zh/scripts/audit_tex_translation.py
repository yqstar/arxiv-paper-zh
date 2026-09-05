#!/usr/bin/env python3
"""Inventory TeX files and flag likely untranslated English prose."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tex_translation_utils import is_bibliography_file, mask_bibliography


PROSE = re.compile(r"[A-Za-z]{4,}(?:[ \t]+[A-Za-z][A-Za-z'’-]{2,}){2,}")
COMMAND_ONLY = re.compile(r"^[ \t]*\\(?:usepackage|documentclass|input|include|addbibresource)\b")


def tex_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.tex")
        if path.is_file() and not is_bibliography_file(path)
    )


def visible_part(line: str) -> str:
    escaped = False
    for index, char in enumerate(line):
        if char == "%" and not escaped:
            return line[:index]
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    return line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--details", action="store_true", help="print all suspect lines; the default shows only the first 10")
    args = parser.parse_args()
    root = args.root.resolve()
    files = tex_files(root)
    if not files:
        parser.error(f"no .tex files below {root}")

    if args.inventory:
        for path in files:
            count = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
            print(f"{count:5d}  {path.relative_to(root)}")
        return 0

    hits = 0
    for path in files:
        text = mask_bibliography(path.read_text(encoding="utf-8", errors="replace"))
        for number, raw in enumerate(text.splitlines(), 1):
            line = visible_part(raw)
            if not line.strip() or COMMAND_ONLY.match(line):
                continue
            if PROSE.search(line):
                hits += 1
                excerpt = line.strip()
                if len(excerpt) > 220:
                    excerpt = excerpt[:217] + "..."
                if args.details or hits <= 10:
                    print(f"{path.relative_to(root)}:{number}: {excerpt}")
    print(f"suspect_lines={hits}")
    if not args.details and hits > 10:
        print(f"more_suspect_lines={hits - 10}; use --details to review all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
