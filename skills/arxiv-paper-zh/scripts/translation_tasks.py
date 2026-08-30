#!/usr/bin/env python3
"""Prepare compact TeX translation packets and safely merge their results."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable, Optional

from tex_translation_utils import is_bibliography_file, mask_bibliography


VERSION = 1
DEFAULT_TASK_DIR = ".translation-tasks"
INCLUDE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
COMMENT = re.compile(r"(?<!\\)%[^\n]*")
COMMAND = re.compile(r"\\(?:[A-Za-z@]+\*?|.)")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
PLACEHOLDER = re.compile(r"⟪T\d{4}⟫")
TEXT_COMMAND = re.compile(
    r"\\(?:title|subtitle|chapter|section|subsection|subsubsection|paragraph|"
    r"subparagraph|caption|footnote|thanks|keyword|keywords)\*?\b",
    re.IGNORECASE,
)
CONTROL_ONLY = re.compile(
    r"^[ \t]*\\(?:documentclass|usepackage|RequirePackage|input|include|"
    r"addbibresource|bibliography|bibliographystyle|includegraphics|label|"
    r"setlength|newcommand|renewcommand|providecommand|def)\b",
    re.IGNORECASE,
)
NON_TEXT_COMMAND = re.compile(
    r"\\(?:documentclass|usepackage|RequirePackage|input|include|"
    r"addbibresource|bibliography|bibliographystyle|includegraphics|label|"
    r"ref|eqref|pageref|autoref|cite\w*|url|path)\*?"
    r"(?:\s*\[[^]]*\])*\s*\{[^{}]*\}",
    re.IGNORECASE,
)
MATH_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<name>equation\*?|align\*?|alignat\*?|gather\*?|"
    r"multline\*?|displaymath|math|eqnarray\*?)\}.*?"
    r"\\end\s*\{(?P=name)\}",
    re.IGNORECASE | re.DOTALL,
)
VERBATIM_ENVIRONMENT = re.compile(
    r"\\begin\s*\{(?P<name>verbatim\*?|lstlisting|minted)\}.*?"
    r"\\end\s*\{(?P=name)\}",
    re.IGNORECASE | re.DOTALL,
)
DISPLAY_MATH = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]", re.DOTALL)
INLINE_MATH = re.compile(r"(?<!\\)\$(?!\$).*?(?<!\\)\$|\\\(.*?\\\)", re.DOTALL)
VERB = re.compile(r"\\verb(?P<delimiter>[^A-Za-z0-9\s]).*?(?P=delimiter)")
REFERENCE_COMMAND = re.compile(
    r"\\(?:cite\w*|ref|eqref|pageref|autoref|label|url|path|"
    r"includegraphics|input|include)\*?(?:\s*\[[^]]*\])*\s*\{[^{}]*\}",
    re.IGNORECASE,
)
HREF_TARGET = re.compile(r"\\href\s*\{[^{}]*\}", re.IGNORECASE)
PACKET_BLOCK = re.compile(
    r"^@@@ SEGMENT (?P<id>\S+)\n"
    r"^@@@ SOURCE\n(?P<source>.*?)"
    r"^@@@ TRANSLATION\n(?P<translation>.*?)"
    r"^@@@ END(?:\n|\Z)",
    re.MULTILINE | re.DOTALL,
)

PACKET_HEADER = """# 只在每个 TRANSLATION 区块填写简体中文译文；不要改 SOURCE 或标记行。
# 严格保留 LaTeX 结构及每个 ⟪T0000⟫ 形式的占位符；保留人名、模型名、数据集名、缩写、数值和引用键。
"""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def reachable(entry: Path, root: Path) -> list[Path]:
    """Return TeX files reachable through input/include, including the entry."""
    pending = [entry.resolve()]
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file() or not _inside(path, root):
            continue
        seen.add(path)
        text = mask_bibliography(path.read_text(encoding="utf-8", errors="replace"))
        for value in INCLUDE.findall(text):
            child = (path.parent / value.strip()).resolve()
            pending.append(child if child.suffix else child.with_suffix(".tex"))
    return sorted(seen)


def _without_protected_text(text: str) -> str:
    for pattern in (
        VERBATIM_ENVIRONMENT,
        MATH_ENVIRONMENT,
        COMMENT,
        DISPLAY_MATH,
        INLINE_MATH,
        VERB,
        REFERENCE_COMMAND,
        HREF_TARGET,
    ):
        text = pattern.sub(" ", text)
    return text


def english_weight(text: str) -> int:
    """Estimate visible English prose words, excluding common TeX controls."""
    text = NON_TEXT_COMMAND.sub(" ", _without_protected_text(text))
    text = COMMAND.sub(" ", text)
    return len([word for word in WORD.findall(text) if len(word) > 1])


def _is_translatable(text: str) -> bool:
    score = english_weight(text)
    return score >= 3 or (score >= 1 and bool(TEXT_COMMAND.search(text) or "&" in text))


def _protect(source: str) -> tuple[str, list[dict[str, str]]]:
    """Replace expensive immutable spans with checked, reversible placeholders."""
    protected: list[dict[str, str]] = []
    masked = source

    def replace(match: re.Match[str]) -> str:
        placeholder = f"⟪T{len(protected):04d}⟫"
        protected.append({"placeholder": placeholder, "value": match.group(0)})
        return placeholder

    for pattern in (
        VERBATIM_ENVIRONMENT,
        MATH_ENVIRONMENT,
        COMMENT,
        DISPLAY_MATH,
        INLINE_MATH,
        VERB,
        REFERENCE_COMMAND,
        HREF_TARGET,
    ):
        masked = pattern.sub(replace, masked)
    return masked, protected


def _paragraph_ranges(masked_text: str) -> Iterable[tuple[int, int]]:
    """Yield zero-based, end-exclusive line ranges separated by hard controls."""
    lines = masked_text.splitlines(keepends=True)
    start: Optional[int] = None
    for index, line in enumerate(lines):
        separator = not line.strip() or bool(CONTROL_ONLY.match(line))
        if separator:
            if start is not None:
                yield start, index
                start = None
        elif start is None:
            start = index
    if start is not None:
        yield start, len(lines)


def _chunk_range(
    lines: list[str], start: int, end: int, chunk_words: int
) -> Iterable[tuple[int, int]]:
    cursor = start
    score = 0
    for index in range(start, end):
        score += english_weight(lines[index])
        if score >= chunk_words and index + 1 < end:
            yield cursor, index + 1
            cursor = index + 1
            score = 0
    if cursor < end:
        yield cursor, end


def segments_for(path: Path, root: Path, chunk_words: int) -> list[dict[str, object]]:
    original = path.read_text(encoding="utf-8", errors="replace")
    bibliography_masked = mask_bibliography(original)
    original_lines = original.splitlines(keepends=True)
    masked_lines = bibliography_masked.splitlines(keepends=True)
    units: list[tuple[int, int, int]] = []

    for paragraph_start, paragraph_end in _paragraph_ranges(bibliography_masked):
        for start, end in _chunk_range(
            masked_lines, paragraph_start, paragraph_end, chunk_words
        ):
            visible = "".join(masked_lines[start:end])
            if not _is_translatable(visible):
                continue
            units.append((start, end, english_weight(visible)))

    merged: list[list[int]] = []
    for start, end, score in units:
        if merged:
            previous = merged[-1]
            gap = "".join(original_lines[previous[1] : start])
            if not gap.strip() and previous[2] + score <= chunk_words:
                previous[1] = end
                previous[2] += score
                continue
        merged.append([start, end, score])

    segments: list[dict[str, object]] = []
    for start, end, score in merged:
        source = "".join(original_lines[start:end])
        masked_source, protected = _protect(source)
        packet_source = (
            masked_source if masked_source.endswith("\n") else masked_source + "\n"
        )
        relative = str(path.relative_to(root))
        identity = f"{relative}:{start + 1}:{end}:".encode() + source.encode()
        segment_id = "s" + hashlib.sha256(identity).hexdigest()[:12]
        segments.append(
            {
                "id": segment_id,
                "path": relative,
                "start_line": start + 1,
                "end_line": end,
                "weight": score,
                "source": source,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "packet_source": packet_source,
                "protected": protected,
            }
        )
    return segments


def _allocate(
    segments: list[dict[str, object]], requested_workers: int, min_words: int
) -> list[list[dict[str, object]]]:
    total = sum(int(segment["weight"]) for segment in segments)
    useful_workers = max(1, math.ceil(total / max(1, min_words)))
    count = min(max(1, requested_workers), useful_workers, max(1, len(segments)))
    groups: list[list[dict[str, object]]] = [[] for _ in range(count)]
    totals = [0] * count
    for segment in sorted(segments, key=lambda item: int(item["weight"]), reverse=True):
        index = min(range(count), key=totals.__getitem__)
        groups[index].append(segment)
        totals[index] += int(segment["weight"])
    for group in groups:
        group.sort(key=lambda item: (str(item["path"]), int(item["start_line"])))
    return groups


def _packet_text(segments: list[dict[str, object]]) -> str:
    parts = [PACKET_HEADER]
    for segment in segments:
        parts.extend(
            [
                f"@@@ SEGMENT {segment['id']}\n",
                "@@@ SOURCE\n",
                str(segment["packet_source"]),
                "@@@ TRANSLATION\n",
                "@@@ END\n",
            ]
        )
    return "".join(parts)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"paper root does not exist: {root}")
    task_dir = (root / args.output).resolve()
    if not _inside(task_dir, root):
        raise SystemExit(f"task directory must stay below paper root: {task_dir}")
    manifest_path = task_dir / "manifest.json"
    if manifest_path.exists() and not args.force:
        raise SystemExit(f"task manifest already exists: {manifest_path}; use --force")
    if args.force and task_dir.is_dir():
        for stale_packet in task_dir.glob("worker-*.task"):
            stale_packet.unlink()

    if args.entry:
        entry = (root / args.entry).resolve()
        if not entry.is_file():
            raise SystemExit(f"entry file does not exist: {entry}")
        files = reachable(entry, root)
    else:
        entry = None
        files = sorted(root.rglob("*.tex"))
    files = [path for path in files if path.is_file() and not is_bibliography_file(path)]
    segments = [
        segment
        for path in files
        for segment in segments_for(path, root, args.chunk_words)
    ]
    if not segments:
        raise SystemExit(f"no translatable TeX prose found below {root}")

    groups = _allocate(segments, args.workers, args.min_words_per_worker)
    packets = []
    for index, group in enumerate(groups, 1):
        name = f"worker-{index:02d}.task"
        _write_atomic(task_dir / name, _packet_text(group))
        packets.append(
            {
                "worker": index,
                "path": name,
                "weight": sum(int(segment["weight"]) for segment in group),
                "segments": [str(segment["id"]) for segment in group],
            }
        )

    scanned_bytes = sum(path.stat().st_size for path in files)
    selected_bytes = sum(len(str(segment["source"]).encode()) for segment in segments)
    packet_source_bytes = sum(
        len(str(segment["packet_source"]).encode()) for segment in segments
    )
    reduction = (
        round(100 * (1 - packet_source_bytes / scanned_bytes), 1)
        if scanned_bytes
        else 0.0
    )
    manifest = {
        "version": VERSION,
        "root": str(root),
        "entry": str(entry.relative_to(root)) if entry else None,
        "packets": packets,
        "segments": segments,
        "metrics": {
            "files": len(files),
            "segments": len(segments),
            "workers": len(groups),
            "visible_english_words": sum(int(segment["weight"]) for segment in segments),
            "scanned_bytes": scanned_bytes,
            "selected_bytes": selected_bytes,
            "packet_source_bytes": packet_source_bytes,
            "input_byte_reduction_percent": reduction,
        },
    }
    _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    public_packets = [
        {
            "worker": packet["worker"],
            "path": packet["path"],
            "weight": packet["weight"],
            "segments": len(packet["segments"]),
        }
        for packet in packets
    ]
    payload = {
        "task_dir": str(task_dir),
        **manifest["metrics"],
        "packets": public_packets,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"prepared {len(segments)} segments for {len(groups)} worker(s); "
            f"estimated input reduction {reduction:.1f}%"
        )
        for packet in packets:
            print(f"{packet['path']}: words={packet['weight']} segments={len(packet['segments'])}")
    return 0


def parse_packet(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, dict[str, str]] = {}
    for match in PACKET_BLOCK.finditer(text):
        segment_id = match.group("id")
        if segment_id in result:
            raise ValueError(f"duplicate segment {segment_id} in {path}")
        result[segment_id] = {
            "source": match.group("source"),
            "translation": match.group("translation"),
        }
    return result


def _load_manifest(root: Path, output: Path) -> tuple[Path, dict[str, object]]:
    task_dir = (root / output).resolve()
    if not _inside(task_dir, root):
        raise SystemExit(f"task directory must stay below paper root: {task_dir}")
    manifest_path = task_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"task manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != VERSION:
        raise SystemExit(f"unsupported task manifest version: {manifest.get('version')}")
    return task_dir, manifest


def _read_results(
    task_dir: Path, manifest: dict[str, object]
) -> tuple[dict[str, dict[str, str]], list[str]]:
    results: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for packet in manifest["packets"]:  # type: ignore[index]
        packet_path = task_dir / packet["path"]
        try:
            parsed = parse_packet(packet_path)
        except (OSError, ValueError) as error:
            errors.append(str(error))
            continue
        for segment_id, value in parsed.items():
            if segment_id in results:
                errors.append(f"duplicate result for {segment_id}")
            results[segment_id] = value
    return results, errors


def status(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    task_dir, manifest = _load_manifest(root, args.output)
    results, errors = _read_results(task_dir, manifest)
    expected = {str(segment["id"]) for segment in manifest["segments"]}  # type: ignore[index]
    completed = {
        segment_id
        for segment_id, value in results.items()
        if value["translation"].strip()
    }
    missing = sorted(expected - completed)
    payload = {
        "segments": len(expected),
        "completed": len(expected) - len(missing),
        "missing": missing,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"completed={payload['completed']}/{payload['segments']}")
        for error in errors:
            print(f"error: {error}")
        for segment_id in missing:
            print(f"missing: {segment_id}")
    return 0 if not missing and not errors else 1


def _restore(translation: str, protected: list[dict[str, str]]) -> str:
    for item in protected:
        translation = translation.replace(item["placeholder"], item["value"])
    return translation


def _structure_signature(text: str) -> tuple[Counter[str], int, int]:
    return Counter(COMMAND.findall(text)), text.count("{"), text.count("}")


def apply(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    task_dir, manifest = _load_manifest(root, args.output)
    results, errors = _read_results(task_dir, manifest)
    replacements: defaultdict[Path, list[tuple[int, int, str]]] = defaultdict(list)
    expected_sources = {
        (str(segment["path"]), int(segment["start_line"]), int(segment["end_line"])): (
            str(segment["source"]),
            str(segment["source_sha256"]),
        )
        for segment in manifest["segments"]  # type: ignore[index]
    }

    for segment in manifest["segments"]:  # type: ignore[index]
        segment_id = str(segment["id"])
        result = results.get(segment_id)
        if result is None:
            errors.append(f"missing segment {segment_id}")
            continue
        if result["source"] != segment["packet_source"]:
            errors.append(f"SOURCE was modified for {segment_id}")
            continue
        translation = result["translation"].lstrip("\n")
        if not translation.strip():
            errors.append(f"empty translation for {segment_id}")
            continue
        expected_placeholders = Counter(
            item["placeholder"] for item in segment["protected"]
        )
        actual_placeholders = Counter(PLACEHOLDER.findall(translation))
        if actual_placeholders != expected_placeholders:
            errors.append(f"placeholder mismatch for {segment_id}")
            continue
        if _structure_signature(translation) != _structure_signature(
            str(segment["packet_source"])
        ):
            errors.append(f"LaTeX structure mismatch for {segment_id}")
            continue

        source = str(segment["source"])
        if source.endswith("\n"):
            if not translation.endswith("\n"):
                translation += "\n"
        else:
            translation = translation.rstrip("\n")
        restored = _restore(translation, segment["protected"])
        path = root / str(segment["path"])
        replacements[path].append(
            (int(segment["start_line"]), int(segment["end_line"]), restored)
        )

    for path, items in replacements.items():
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        for start, end, _translation in items:
            current = "".join(lines[start - 1 : end])
            expected, expected_hash = expected_sources[
                (str(path.relative_to(root)), start, end)
            ]
            if (
                current != expected
                or hashlib.sha256(current.encode()).hexdigest() != expected_hash
            ):
                errors.append(f"source changed since prepare: {path.relative_to(root)}:{start}-{end}")

    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    if args.check:
        print(f"validated {sum(len(items) for items in replacements.values())} translations")
        return 0

    for path, items in replacements.items():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for start, end, translation in sorted(items, reverse=True):
            lines[start - 1 : end] = [translation]
        _write_atomic(path, "".join(lines))
    print(
        f"applied {sum(len(items) for items in replacements.values())} translations "
        f"to {len(replacements)} file(s)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="create compact worker packets")
    prepare_parser.add_argument("root", type=Path)
    prepare_parser.add_argument("--entry", type=Path)
    prepare_parser.add_argument("--workers", type=int, default=3)
    prepare_parser.add_argument("--chunk-words", type=int, default=900)
    prepare_parser.add_argument("--min-words-per-worker", type=int, default=1200)
    prepare_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    prepare_parser.add_argument("--force", action="store_true")
    prepare_parser.add_argument("--json", action="store_true")
    prepare_parser.set_defaults(handler=prepare)

    status_parser = subparsers.add_parser("status", help="report packet completion")
    status_parser.add_argument("root", type=Path)
    status_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=status)

    apply_parser = subparsers.add_parser("apply", help="validate and merge packet translations")
    apply_parser.add_argument("root", type=Path)
    apply_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    apply_parser.add_argument("--check", action="store_true")
    apply_parser.set_defaults(handler=apply)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "workers", 1) < 1:
        parser.error("--workers must be positive")
    if getattr(args, "chunk_words", 1) < 1:
        parser.error("--chunk-words must be positive")
    if getattr(args, "min_words_per_worker", 1) < 1:
        parser.error("--min-words-per-worker must be positive")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
