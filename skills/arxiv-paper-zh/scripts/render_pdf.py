#!/usr/bin/env python3
"""Render requested PDF pages, reusing PNGs whose content hashes still match."""
from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time

from artifact_cache import file_hash, load_cache, save_cache, value_hash


def executable(value: str) -> Path:
    found = shutil.which(value)
    if not found:
        raise ValueError(f"missing executable: {value}")
    return Path(found).absolute()


def command_output(command: list[str], log: Path) -> str:
    # Keep diagnostics on disk, including successful renderer/font warnings.
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                                env={**os.environ, "LC_ALL": "C"})
    if result.returncode:
        with log.open(encoding="utf-8", errors="replace") as output:
            tail = " | ".join(line.rstrip()[:240] for line in deque(output, maxlen=5))
        raise ValueError(f"{Path(command[0]).name} failed ({result.returncode}); log={log}; {tail}")
    return log.read_text(encoding="utf-8", errors="replace")


def selected_pages(specification: str | None, count: int) -> list[int]:
    if not specification:
        return list(range(1, count + 1))
    pages = set()
    for part in specification.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise ValueError("--pages must contain page numbers or ranges, e.g. 1,3-5")
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ValueError(f"page range {part.strip()} is outside 1-{count}")
        pages.update(range(first, last + 1))
    return sorted(pages)


def consecutive_ranges(pages: list[int]):
    if not pages:
        return
    first = last = pages[0]
    for page in pages[1:]:
        if page == last + 1:
            last = page
        else:
            yield first, last
            first = last = page
    yield first, last


def png_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as image:
        header = image.read(24)
    if len(header) < 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    return file_hash(path) if width and height else None


def render(args) -> dict:
    started = time.monotonic()
    pdf = args.pdf.resolve()
    digest = file_hash(pdf)
    if not digest:
        raise ValueError(f"missing PDF: {pdf}")
    renderer, info = executable(args.pdftoppm), executable(args.pdfinfo)
    config = {"pdf_hash": digest, "dpi": args.dpi,
              "tools": {str(path): file_hash(path) for path in (renderer, info)},
              "environment": {name: os.environ.get(name) for name in ("FONTCONFIG_PATH", "FONTCONFIG_FILE", "XDG_DATA_HOME", "XDG_DATA_DIRS", "HOME")},
              "script": [file_hash(Path(__file__)), file_hash(Path(__file__).with_name("artifact_cache.py"))]}
    directory = args.output.resolve() / value_hash(config)[:24]
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    manifest = load_cache(manifest_path)
    if manifest.get("version") != 1 or manifest.get("config") != config or not isinstance(manifest.get("pages"), dict):
        manifest = {"version": 1, "config": config, "pages": {}}
    count = manifest.get("page_count")
    if args.force or not isinstance(count, int) or isinstance(count, bool) or count < 1:
        output = command_output([str(info), str(pdf)], directory / "pdfinfo.log")
        match = re.search(r"^Pages:\s*(\d+)\s*$", output, re.M)
        if not match or int(match[1]) < 1:
            raise ValueError(f"pdfinfo did not report a positive page count; log={directory / 'pdfinfo.log'}")
        count = int(match[1])
        manifest["page_count"] = count
    pages = selected_pages(args.pages, count)
    paths = {page: directory / f"page-{page:04d}.png" for page in pages}
    missing = [page for page in pages if args.force or not isinstance(manifest["pages"].get(str(page)), str)
               or png_hash(paths[page]) != manifest["pages"][str(page)]]
    for first, last in consecutive_ranges(missing):
        # Commit a whole range only after its complete output and PDF input pass validation.
        stage = Path(tempfile.mkdtemp(prefix=".render-", dir=directory))
        log = directory / f"render-{first}-{last}.log"
        command_output([str(renderer), "-f", str(first), "-l", str(last), "-r", str(args.dpi),
                        "-png", str(pdf), str(stage / "page")], log)
        images = {}
        for path in stage.glob("page-*.png"):
            match = re.fullmatch(r"page-(\d+)\.png", path.name)
            if match:
                images[int(match[1])] = path
        hashes = {page: png_hash(images[page]) if page in images else None for page in range(first, last + 1)}
        if not all(hashes.values()):
            raise ValueError(f"renderer omitted or produced invalid pages; staging={stage}; log={log}")
        if file_hash(pdf) != digest:
            raise ValueError("PDF changed while rendering; rerun with a stable PDF")
        for page, expected in hashes.items():
            os.replace(images[page], paths[page])
            manifest["pages"][str(page)] = expected
        save_cache(manifest_path, manifest)
        shutil.rmtree(stage)
    if file_hash(pdf) != digest:
        raise ValueError("PDF changed while checking cache; rerun with a stable PDF")
    return {"ok": True, "pdf": str(pdf), "render_dir": str(directory), "page_count": count,
            "requested_count": len(pages), "rendered": len(missing), "reused": len(pages) - len(missing),
            "dpi": args.dpi, "elapsed_seconds": round(time.monotonic() - started, 3),
            "files": [str(paths[page]) for page in pages] if args.details else [str(paths[page]) for page in pages[:3]],
            "file_pattern": str(directory / "page-NNNN.png")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="render cache root, normally <paper>/tmp/render-en or render-zh")
    parser.add_argument("--dpi", type=int, default=90)
    parser.add_argument("--pages", help="one-based page numbers/ranges; default is every page")
    parser.add_argument("--pdftoppm", default="pdftoppm", help="executable name or full path")
    parser.add_argument("--pdfinfo", default="pdfinfo", help="executable name or full path")
    parser.add_argument("--force", action="store_true", help="render requested pages again, including after font/CMap repairs")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--details", action="store_true", help="list every requested image path")
    args = parser.parse_args()
    if args.dpi < 1:
        parser.error("--dpi must be positive")
    try:
        payload = render(args)
    except (OSError, ValueError) as error:
        payload = {"ok": False, "error": str(error)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    elif payload["ok"]:
        print(f"render_ok={payload['render_dir']} pages={payload['page_count']} requested={payload['requested_count']} rendered={payload['rendered']} reused={payload['reused']} dpi={payload['dpi']} elapsed_seconds={payload['elapsed_seconds']}")
        for path in payload["files"]:
            print(path)
    else:
        print("render_error=" + payload["error"])
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
