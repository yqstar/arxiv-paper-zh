#!/usr/bin/env python3
"""Prepare compact TeX translation packets and safely merge their results."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable, Optional

from tex_translation_utils import is_bibliography_file, mask_bibliography


VERSION = 3
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

PACKET_HEADER = r"""# 本文件只读。将全部 SOURCE 译为简体中文，只写指定的结果文件。
# 结果为 JSONL，每行只有 id 和 translation 两个字符串字段，不回显原文，不加 Markdown 围栏。
# 用 JSON 转义 LaTeX 反斜杠、双引号和换行，例如 {"id":"s123","translation":"\\section{引言}\n正文。"}。
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


def _batch_segments(
    segments: list[dict[str, object]], packet_words: int
) -> list[list[dict[str, object]]]:
    """Keep adjacent segments together without ever exceeding the word budget."""
    groups: list[list[dict[str, object]]] = []
    weight = 0
    for segment in segments:
        score = int(segment["weight"])
        if score > packet_words:
            raise SystemExit(
                f"segment {segment['id']} at {segment['path']}:"
                f"{segment['start_line']}-{segment['end_line']} has {score} words, "
                f"exceeding --packet-words {packet_words}; reflow this source "
                "before prepare or explicitly increase --packet-words"
            )
        if not groups or weight + score > packet_words:
            groups.append([])
            weight = 0
        groups[-1].append(segment)
        weight += score
    return groups


def _packet_text(segments: list[dict[str, object]], result_path: str) -> str:
    parts = [PACKET_HEADER, f"# 结果文件（与本文件同目录）：{result_path}\n"]
    for segment in segments:
        parts.extend(
            [
                f"@@@ SEGMENT {segment['id']}\n",
                "@@@ SOURCE\n",
                str(segment["packet_source"]),
                "@@@ END\n",
            ]
        )
    return "".join(parts)


def _packet_summary(packet: dict[str, object]) -> dict[str, object]:
    return {
        "path": packet["path"],
        "result_path": packet.get("result_path", packet["path"]),
        "words": packet["weight"],
        "segments": len(packet["segments"]),
    }


def _print_errors(errors: list[str], details: bool) -> None:
    for error in errors if details else errors[:5]:
        print(f"error: {error if details else error[:300]}")
    if not details and len(errors) > 5:
        print(f"more_errors={len(errors) - 5}; use --details")


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
    if manifest_path.exists() and args.resume:
        if args.entry:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("entry") != str(args.entry):
                raise SystemExit("--entry differs from the existing task; resume without changing its entry")
        args.command = "resume"
        return progress_command(args)
    if manifest_path.exists() and not args.force:
        raise SystemExit(f"task manifest already exists: {manifest_path}; use --resume to continue or --force to replace")
    journal = task_dir / ".merge" / "journal.json"
    if journal.is_file() and json.loads(journal.read_text(encoding="utf-8")).get("phase") == "applying":
        raise SystemExit("unfinished merge; run resume before preparing new tasks")
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
        for segment in segments_for(path, root, min(args.chunk_words, args.packet_words))
    ]
    if not segments:
        raise SystemExit(f"no translatable TeX prose found below {root}")

    groups = _batch_segments(segments, args.packet_words)
    total_words = sum(int(segment["weight"]) for segment in segments)
    workers = min(args.workers, max(1, math.ceil(total_words / args.min_words_per_worker)), len(groups))
    if args.force and task_dir.is_dir():
        for pattern in ("worker-*.task", "packet-*.task", "packet-*.result.jsonl"):
            for stale_packet in task_dir.glob(pattern):
                stale_packet.unlink()
        for name in (".checks", ".repairs", ".merge"):
            path = task_dir / name
            if path.is_dir():
                shutil.rmtree(path)
    packets = []
    for index, group in enumerate(groups, 1):
        name = f"packet-{index:04d}.task"
        result_name = f"packet-{index:04d}.result.jsonl"
        packet_text = _packet_text(group, result_name)
        _write_atomic(task_dir / name, packet_text)
        packets.append(
            {
                "path": name,
                "result_path": result_name,
                "sha256": hashlib.sha256(packet_text.encode()).hexdigest(),
                "weight": sum(int(segment["weight"]) for segment in group),
                "segments": [str(segment["id"]) for segment in group],
            }
        )

    scanned_bytes = sum(path.stat().st_size for path in files)
    selected_bytes = sum(len(str(segment["source"]).encode()) for segment in segments)
    packet_source_bytes = sum(
        len(str(segment["packet_source"]).encode()) for segment in segments
    )
    packet_bytes = sum((task_dir / str(packet["path"])).stat().st_size for packet in packets)
    reduction = (
        round(100 * (1 - packet_bytes / scanned_bytes), 1)
        if scanned_bytes
        else 0.0
    )
    manifest = {
        "version": VERSION,
        "root": str(root),
        "entry": str(entry.relative_to(root)) if entry else None,
        "source_files": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "packets": packets,
        "segments": segments,
        "metrics": {
            "files": len(files),
            "segments": len(segments),
            "workers": workers,
            "packet_count": len(groups),
            "packet_words": args.packet_words,
            "visible_english_words": total_words,
            "scanned_bytes": scanned_bytes,
            "selected_bytes": selected_bytes,
            "packet_source_bytes": packet_source_bytes,
            "packet_bytes": packet_bytes,
            "input_byte_reduction_percent": reduction,
        },
    }
    _write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    payload = {
        "task_dir": str(task_dir),
        **manifest["metrics"],
        "packets": [_packet_summary(packet) for packet in (packets if args.details else packets[:workers])],
        "listed_packets": len(packets) if args.details else workers,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(
            f"prepared segments={len(segments)} packets={len(groups)} workers={workers} "
            f"words={total_words} packet_words={args.packet_words}"
        )
        print(f"task_dir={task_dir}")
        for packet in payload["packets"]:
            print(f"next: {packet['path']} -> {packet['result_path']} words={packet['words']}")
        if len(packets) > payload["listed_packets"]:
            print("more packets queued; use status for the next batch or --details for all")
    return 0


def parse_packet(path: Path) -> dict[str, dict[str, str]]:
    """Read legacy version-1 packets so in-progress translations remain usable."""
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


def parse_results(path: Path) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    if not path.exists():
        return results
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as error:
            raise ValueError(f"{path.name}:{number}: invalid JSONL; escape backslashes and newlines") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"id", "translation"}
            or not isinstance(value["id"], str)
            or not isinstance(value["translation"], str)
        ):
            raise ValueError(f"{path.name}:{number}: expected only string fields id and translation")
        segment_id = value["id"]
        if segment_id in results:
            raise ValueError(f"duplicate segment {segment_id} in {path.name}")
        results[segment_id] = {"translation": value["translation"]}
    return results


def _load_manifest(root: Path, output: Path) -> tuple[Path, dict[str, object]]:
    task_dir = (root / output).resolve()
    if not _inside(task_dir, root):
        raise SystemExit(f"task directory must stay below paper root: {task_dir}")
    manifest_path = task_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"task manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") not in (1, 2, VERSION):
        raise SystemExit(f"unsupported task manifest version: {manifest.get('version')}")
    return task_dir, manifest


def progress_command(args: argparse.Namespace) -> int:
    from translation_progress import dispatch

    return dispatch(args)


def _restore(translation: str, protected: list[dict[str, str]]) -> str:
    for item in protected:
        translation = translation.replace(item["placeholder"], item["value"])
    return translation


def _structure_signature(text: str) -> tuple[Counter[str], int, int]:
    return Counter(COMMAND.findall(text)), text.count("{"), text.count("}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="create compact worker packets")
    prepare_parser.add_argument("root", type=Path)
    prepare_parser.add_argument("--entry", type=Path)
    prepare_parser.add_argument("--workers", type=int, default=3)
    prepare_parser.add_argument("--chunk-words", type=int, default=900)
    prepare_parser.add_argument("--packet-words", type=int, default=2000, help="maximum visible English words per packet; independent of worker count")
    prepare_parser.add_argument("--min-words-per-worker", type=int, default=1200)
    prepare_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    restart = prepare_parser.add_mutually_exclusive_group()
    restart.add_argument("--force", action="store_true")
    restart.add_argument("--resume", action="store_true", help="continue existing tasks without regenerating packets")
    prepare_parser.add_argument("--json", action="store_true")
    prepare_parser.add_argument("--details", action="store_true", help="list every packet instead of only the first worker batch")
    prepare_parser.set_defaults(handler=prepare)

    status_parser = subparsers.add_parser("status", help="report packet completion")
    status_parser.add_argument("root", type=Path)
    status_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--details", action="store_true", help="list all pending packets, missing segments and errors")
    status_parser.set_defaults(handler=progress_command)

    apply_parser = subparsers.add_parser("apply", help="validate and merge packet translations")
    apply_parser.add_argument("root", type=Path)
    apply_parser.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
    apply_parser.add_argument("--check", action="store_true")
    apply_parser.add_argument("--json", action="store_true")
    apply_parser.add_argument("--details", action="store_true", help="show all validation errors")
    apply_parser.set_defaults(handler=progress_command)
    for name, help_text in (
        ("check", "validate one finished packet and save its checkpoint"),
        ("repair", "prepare a repair containing only failed segments, or apply its result"),
        ("resume", "restore progress and finish an interrupted merge"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("root", type=Path)
        command.add_argument("--output", type=Path, default=Path(DEFAULT_TASK_DIR))
        command.add_argument("--json", action="store_true")
        command.add_argument("--details", action="store_true")
        if name in ("check", "repair"):
            command.add_argument("--packet", required=True, help="packet filename or its absolute path")
        if name == "repair":
            command.add_argument("--apply", action="store_true", help="validate and merge the active repair result")
        command.set_defaults(handler=progress_command)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "workers", 1) < 1:
        parser.error("--workers must be positive")
    if getattr(args, "chunk_words", 1) < 1:
        parser.error("--chunk-words must be positive")
    if getattr(args, "packet_words", 1) < 1:
        parser.error("--packet-words must be positive")
    if getattr(args, "min_words_per_worker", 1) < 1:
        parser.error("--min-words-per-worker must be positive")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
