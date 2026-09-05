"""Small file-based helpers for disposable build and render caches."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def value_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_cache(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def hashes_match(files: dict) -> bool:
    return isinstance(files, dict) and bool(files) and all(
        isinstance(path, str) and isinstance(expected, str) and file_hash(Path(path)) == expected
        for path, expected in files.items())
