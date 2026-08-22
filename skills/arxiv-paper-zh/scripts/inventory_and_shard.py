#!/usr/bin/env python3
"""Inventory translatable TeX sources and greedily balance translation shards."""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

from tex_translation_utils import is_bibliography_file, mask_bibliography

INCLUDE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
COMMENT = re.compile(r"(?<!\\)%.*$")
COMMAND = re.compile(r"\\[A-Za-z@]+\*?(?:\[[^]]*\])?")
MATH = re.compile(r"\$[^$]*\$|\\\([^)]*\\\)|\\\[[^]]*\\\]", re.S)
def reachable(entry: Path, root: Path) -> list[Path]:
    pending, seen = [entry.resolve()], set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file() or (root not in path.parents and path != root): continue
        seen.add(path)
        text = mask_bibliography(path.read_text(encoding="utf-8", errors="replace"))
        for value in INCLUDE.findall(text):
            child = (path.parent / value.strip()).resolve()
            pending.append(child if child.suffix else child.with_suffix(".tex"))
    return sorted(seen)
def weight(path: Path) -> int:
    text = mask_bibliography(path.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(COMMENT.sub("", line) for line in text.splitlines())
    return len(re.findall(r"[A-Za-z]{2,}", COMMAND.sub("", MATH.sub("", text))))
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); parser.add_argument("--entry", type=Path); parser.add_argument("--workers", type=int, default=3); parser.add_argument("--min-weight", type=int, default=1); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    root = args.root.resolve(); entry = (root / args.entry).resolve() if args.entry else None
    files = reachable(entry, root) if entry else sorted(root.rglob("*.tex")); shared = {"macro.tex", "macros.tex", "commands.tex"}; records = [(p, weight(p)) for p in files if p.is_file() and p != entry and p.name.lower() not in shared and not is_bibliography_file(p)]; records = [(p, score) for p, score in records if score >= args.min_weight]
    groups = [[] for _ in range(max(1, args.workers))]; totals = [0] * len(groups)
    for path, score in sorted(records, key=lambda item: item[1], reverse=True):
        index = min(range(len(groups)), key=totals.__getitem__); groups[index].append((path, score)); totals[index] += score
    payload = [{"worker": i + 1, "weight": totals[i], "files": [str(p.relative_to(root)) for p, _ in group]} for i, group in enumerate(groups)]
    if args.json: print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for group in payload:
            print(f"worker={group['worker']} weight={group['weight']}"); [print(f"  {p}") for p in group["files"]]
    return 0
if __name__ == "__main__": raise SystemExit(main())
