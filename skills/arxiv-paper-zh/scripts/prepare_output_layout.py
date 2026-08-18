#!/usr/bin/env python3
"""Create the deterministic artifact layout for one translated paper."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper_name", help="short, filesystem-safe paper name, e.g. EST or Onetrans")
    parser.add_argument("--root", type=Path, default=Path("arxiv-paper"))
    args = parser.parse_args()

    name = args.paper_name.strip()
    if not SAFE_NAME.fullmatch(name):
        parser.error("paper_name must match [A-Za-z0-9][A-Za-z0-9._-]*")

    paper_root = (args.root / name).resolve()
    paths = {
        "paper_root": paper_root,
        "latex_root": paper_root / "latex",
        "source_archive": paper_root / "latex" / "source.tar",
        "latex_en": paper_root / "latex" / "paper-en",
        "latex_zh": paper_root / "latex" / "paper-zh",
        "pdf_en": paper_root / "paper-en" / f"{name}-en.pdf",
        "pdf_zh": paper_root / "paper-zh" / f"{name}-zh.pdf",
    }
    for key in ("latex_en", "latex_zh"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["pdf_en"].parent.mkdir(parents=True, exist_ok=True)
    paths["pdf_zh"].parent.mkdir(parents=True, exist_ok=True)

    print(json.dumps({key: str(value) for key, value in paths.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
