#!/usr/bin/env python3
"""Validate one paper's deliverables and remove its managed tmp directory."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def has_files(path: Path) -> bool:
    return path.is_dir() and any(item.is_file() for item in path.rglob("*"))


def remove_path(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper_root", type=Path)
    args = parser.parse_args()

    paper_root = args.paper_root.resolve()
    name = paper_root.name
    if not paper_root.is_dir():
        parser.error(f"paper root does not exist: {paper_root}")
    if not SAFE_NAME.fullmatch(name):
        parser.error("paper directory name must match [A-Za-z0-9][A-Za-z0-9._-]*")

    source_archive = paper_root / "latex" / "source.tar"
    pdf_en = paper_root / "paper-en" / f"{name}-en.pdf"
    pdf_zh = paper_root / "paper-zh" / f"{name}-zh.pdf"
    required_files = {
        "source archive": source_archive,
        "English PDF": pdf_en,
        "Chinese PDF": pdf_zh,
    }
    required_sources = {
        "English source": paper_root / "latex" / "paper-en",
        "Chinese source": paper_root / "latex" / "paper-zh",
    }
    forbidden_staging = (
        paper_root / "source",
        paper_root / "source.tar",
        paper_root / "latex" / "source",
    )

    errors = [
        f"missing or empty {label}: {path}"
        for label, path in required_files.items()
        if not path.is_file() or not path.stat().st_size
    ]
    errors.extend(
        f"missing or empty {label} directory: {path}"
        for label, path in required_sources.items()
        if not has_files(path)
    )
    errors.extend(
        f"unexpected source staging path: {path}; use {source_archive}"
        for path in forbidden_staging
        if path.exists() or path.is_symlink()
    )
    if errors:
        for error in errors:
            print(f"error: {error}")
        print("tmp_preserved=true")
        return 1

    temp_root = paper_root / "tmp"
    removed_tmp = remove_path(temp_root)
    print(
        json.dumps(
            {
                "paper_root": str(paper_root),
                "source_archive": str(source_archive),
                "removed_tmp": removed_tmp,
                "status": "complete",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
