#!/usr/bin/env python3
"""Validate packets, prepare local repairs, and resume journaled translation merges."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

import translation_tasks as tasks


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_digest(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    tasks._write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def file_digest(path: Path) -> str | None:
    return digest(path.read_bytes()) if path.is_file() else None


def translation_errors(segment: dict, translation: str) -> list[str]:
    if not translation.strip():
        return ["empty translation"]
    errors = []
    expected = Counter(item["placeholder"] for item in segment["protected"])
    if Counter(tasks.PLACEHOLDER.findall(translation)) != expected:
        errors.append("placeholder mismatch")
    if tasks._structure_signature(translation) != tasks._structure_signature(segment["packet_source"]):
        errors.append("LaTeX structure mismatch")
    return errors


class Progress:
    def __init__(self, root: Path, output: Path):
        self.root = root.resolve()
        self.task_dir, self.manifest = tasks._load_manifest(self.root, output)
        self.key = file_digest(self.task_dir / "manifest.json")
        self.segments = {item["id"]: item for item in self.manifest["segments"]}
        self.packets = self.manifest["packets"]
        self.journal_path = self.task_dir / ".merge" / "journal.json"
        self.rules = digest(Path(__file__).read_bytes() + Path(tasks.__file__).read_bytes())
        self.source_hashes = {}
        self.source_errors = []
        expected_files = self.manifest.get("source_files", {})
        paths = set(expected_files) | {item["path"] for item in self.segments.values()}
        for relative in sorted(paths):
            path = self.root / relative
            current = file_digest(path)
            self.source_hashes[relative] = current
            if current is None or (relative in expected_files and current != expected_files[relative]):
                self.source_errors.append(f"source changed since prepare: {relative}")
        # Version 1/2 manifests have only segment snapshots, not complete file hashes.
        if not expected_files:
            for relative in sorted(paths):
                path = self.root / relative
                if not path.is_file():
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                for segment in self.segments.values():
                    if segment["path"] != relative:
                        continue
                    current = "".join(lines[segment["start_line"] - 1:segment["end_line"]])
                    if digest(current.encode()) != segment["source_sha256"]:
                        self.source_errors.append(f"source changed since prepare: {relative}:{segment['start_line']}")

    def packet(self, selector: str) -> dict:
        for packet in self.packets:
            if selector == packet["path"] or Path(selector).resolve() == (self.task_dir / packet["path"]).resolve():
                return packet
        raise ValueError(f"unknown packet: {selector}")

    def result_path(self, packet: dict) -> Path:
        return self.task_dir / packet.get("result_path", packet["path"])

    def inspect(self, packet: dict, persist: bool = False) -> dict:
        packet_path = self.task_dir / packet["path"]
        blocked = list(self.source_errors)
        input_bytes = packet_path.read_bytes() if packet_path.is_file() else b""
        if not input_bytes:
            blocked.append(f"missing packet: {packet['path']}")
        elif self.manifest["version"] != 1 and digest(input_bytes) != packet["sha256"]:
            blocked.append(f"read-only packet was modified: {packet['path']}")
        result_path = self.result_path(packet)
        result_bytes = result_path.read_bytes() if result_path.is_file() else b""
        fingerprint = json_digest([self.key, self.rules, self.source_hashes, digest(input_bytes), digest(result_bytes)])
        cache_path = self.task_dir / ".checks" / f"{Path(packet['path']).stem}.json"
        if cache_path.is_file() and not blocked:
            try:
                cached = read_json(cache_path)
                if cached.get("fingerprint") == fingerprint:
                    return cached
            except (ValueError, OSError):
                pass
        result = {
            "fingerprint": fingerprint, "result_hash": digest(result_bytes),
            "has_result": bool(result_bytes.strip()), "valid": {}, "issues": {},
            "current": {}, "missing": [], "file_errors": [], "blocked": blocked,
        }
        records = {}
        expected = set(packet["segments"])
        try:
            text = result_bytes.decode("utf-8")
        except UnicodeError:
            text = ""
            result["file_errors"].append("result is not UTF-8")
        if self.manifest["version"] == 1:
            for match in tasks.PACKET_BLOCK.finditer(text):
                segment_id = match.group("id")
                if segment_id not in expected:
                    result["file_errors"].append(f"unexpected segment {segment_id}")
                    continue
                if segment_id in records:
                    result["issues"].setdefault(segment_id, []).append("duplicate segment")
                records[segment_id] = match.group("translation")
                if match.group("source") != self.segments[segment_id]["packet_source"]:
                    result["blocked"].append(f"SOURCE was modified for {segment_id}")
        else:
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    hint = re.match(r'^\s*\{\s*"id"\s*:\s*"([^"\\]+)"', line)
                    segment_id = hint.group(1) if hint else None
                    error = f"invalid JSONL at line {number}; escape backslashes and newlines"
                    if segment_id in expected:
                        result["issues"].setdefault(segment_id, []).append(error)
                        result["current"][segment_id] = line
                    else:
                        result["file_errors"].append(error)
                    continue
                segment_id = value.get("id") if isinstance(value, dict) else None
                if not isinstance(segment_id, str) or segment_id not in expected:
                    result["file_errors"].append(f"unexpected segment at line {number}: {str(segment_id)[:80]}")
                    continue
                if set(value) != {"id", "translation"} or not isinstance(value.get("translation"), str):
                    result["issues"].setdefault(segment_id, []).append("expected only string fields id and translation")
                    result["current"][segment_id] = value.get("translation", "") if isinstance(value.get("translation"), str) else line
                    continue
                if segment_id in records:
                    result["issues"].setdefault(segment_id, []).append("duplicate segment")
                records[segment_id] = value["translation"]
        for segment_id in packet["segments"]:
            translation = records.get(segment_id)
            if translation is None:
                if segment_id not in result["issues"]:
                    result["missing"].append(segment_id)
                continue
            result["current"][segment_id] = translation
            errors = translation_errors(self.segments[segment_id], translation)
            if errors:
                result["issues"].setdefault(segment_id, []).extend(errors)
            if segment_id not in result["issues"] and not result["blocked"]:
                result["valid"][segment_id] = translation
        if persist:
            write_json(cache_path, result)
        return result

    def repair_metadata_path(self, packet: dict) -> Path:
        return self.task_dir / ".repairs" / f"{Path(packet['path']).stem}.json"

    def active_repair(self, packet: dict, inspection: dict) -> dict | None:
        path = self.repair_metadata_path(packet)
        if not path.is_file():
            return None
        metadata = read_json(path)
        if metadata.get("fingerprint") != inspection["fingerprint"]:
            return None
        return metadata

    def report(self, packets: list[dict], persist: bool = False) -> dict:
        valid = 0
        blocked = False
        missing, invalid, errors, pending = [], [], [], []
        for packet in packets:
            result = self.inspect(packet, persist)
            valid += len(result["valid"])
            missing.extend(result["missing"])
            invalid.extend(result["issues"])
            errors.extend(result["blocked"] + result["file_errors"])
            blocked = blocked or bool(result["blocked"])
            errors.extend(f"{segment_id}: {error}" for segment_id, items in result["issues"].items() for error in items)
            if len(result["valid"]) != len(packet["segments"]) or result["file_errors"] or result["blocked"]:
                summary = tasks._packet_summary(packet)
                summary["validated"] = len(result["valid"])
                summary["action"] = "blocked" if result["blocked"] else "repair" if result["has_result"] else "translate"
                active = self.active_repair(packet, result)
                if active and not result["blocked"]:
                    summary["repair_path"] = active["path"]
                    summary["repair_result_path"] = active["result_path"]
                    summary["action"] = "repair-apply" if (self.task_dir / active["result_path"]).is_file() else "repair"
                pending.append(summary)
        total = sum(len(packet["segments"]) for packet in packets)
        phase = "blocked" if blocked else "ready" if not pending else "needs_repair" if errors else "translating"
        return {
            "task_dir": str(self.task_dir), "phase": phase, "segments": total,
            "completed": valid, "validated": valid, "missing_count": len(missing),
            "invalid_count": len(invalid), "error_count": len(errors), "missing": missing,
            "invalid": invalid, "errors": list(dict.fromkeys(errors)),
            "pending_packets": len(pending), "next_packets": pending,
            "next_action": "inspect_inputs" if blocked else "apply" if phase == "ready" else "process_packets",
        }

    def write_results(self, packet: dict, translations: dict[str, str]) -> None:
        if self.manifest["version"] == 1:
            text = "# 旧版任务：只编辑 TRANSLATION。\n" + "".join(
                f"@@@ SEGMENT {segment_id}\n@@@ SOURCE\n{self.segments[segment_id]['packet_source']}"
                f"@@@ TRANSLATION\n{translations[segment_id].rstrip(chr(10))}\n@@@ END\n"
                for segment_id in packet["segments"]
            )
        else:
            text = "".join(json.dumps({"id": segment_id, "translation": translations[segment_id]}, ensure_ascii=False) + "\n" for segment_id in packet["segments"])
        tasks._write_atomic(self.result_path(packet), text)

    def repair(self, packet: dict, accept: bool) -> dict:
        result = self.inspect(packet, persist=True)
        if result["blocked"]:
            raise ValueError("; ".join(result["blocked"]))
        if len(result["valid"]) == len(packet["segments"]) and not result["file_errors"]:
            return {"phase": "validated", "packet": packet["path"], "repaired": 0, "preserved": len(result["valid"])}
        if accept:
            metadata = self.active_repair(packet, result)
            if not metadata:
                raise ValueError("repair is missing or stale; generate a repair for the current result first")
            if file_digest(self.task_dir / metadata["path"]) != metadata["sha256"]:
                raise ValueError("read-only repair packet was modified")
            corrected = tasks.parse_results(self.task_dir / metadata["result_path"])
            if set(corrected) != set(metadata["segments"]):
                raise ValueError("repair result must contain exactly its requested segment IDs")
            for segment_id, row in corrected.items():
                errors = translation_errors(self.segments[segment_id], row["translation"])
                if errors:
                    raise ValueError(f"{segment_id}: {'; '.join(errors)}")
            translations = {**result["valid"], **{key: row["translation"] for key, row in corrected.items()}}
            self.write_results(packet, translations)
            return {"phase": "repaired", "packet": packet["path"], "repaired": len(corrected), "preserved": len(result["valid"])}
        failed = [segment_id for segment_id in packet["segments"] if segment_id not in result["valid"]]
        if not failed:
            if result["file_errors"]:
                # All expected translations are valid. Remove only unowned/malformed extra rows.
                backup = self.task_dir / ".repairs" / f"{Path(packet['path']).stem}-{result['result_hash'][:12]}.original"
                tasks._write_atomic(backup, self.result_path(packet).read_text(encoding="utf-8"))
                self.write_results(packet, result["valid"])
            return {"phase": "validated", "packet": packet["path"], "repaired": 0, "preserved": len(result["valid"])}
        active = self.active_repair(packet, result)
        if active:
            return {"phase": "repair_pending", **{key: active[key] for key in ("path", "result_path", "segments")}}
        name = f"{Path(packet['path']).stem}-{result['fingerprint'][:12]}"
        path = self.task_dir / ".repairs" / f"{name}.task"
        result_path = path.with_suffix(".result.jsonl")
        parts = [tasks.PACKET_HEADER, f"# 修复任务：只修复以下片段，保留正确译文的含义。结果写入同目录 {result_path.name}\n"]
        for segment_id in failed:
            segment = self.segments[segment_id]
            current = result["current"].get(segment_id, "")
            limit = max(1000, len(segment["packet_source"]) * 2)
            if len(current) > limit:
                current = current[:limit] + "\n[当前译文过长，已截断；以 SOURCE 为准]"
            errors = result["issues"].get(segment_id, ["missing translation"])
            parts.extend([
                f"@@@ SEGMENT {segment_id}\n@@@ SOURCE\n{segment['packet_source']}",
                f"@@@ CURRENT\n{current}\n@@@ ERRORS\n{' ; '.join(errors)}\n@@@ END\n",
            ])
        text = "".join(parts)
        tasks._write_atomic(path, text)
        metadata = {
            "fingerprint": result["fingerprint"], "path": str(path.relative_to(self.task_dir)),
            "result_path": str(result_path.relative_to(self.task_dir)), "sha256": digest(text.encode()),
            "segments": failed,
        }
        write_json(self.repair_metadata_path(packet), metadata)
        return {"phase": "repair_pending", **{key: metadata[key] for key in ("path", "result_path", "segments")}, "preserved": len(result["valid"])}

    def journal(self) -> dict | None:
        if not self.journal_path.is_file():
            return None
        value = read_json(self.journal_path)
        if value.get("version", 1) != 1:
            raise ValueError("unsupported merge journal version")
        if value.get("manifest_hash") != self.key:
            raise ValueError("merge journal belongs to a different manifest")
        return value

    def merged_report(self, journal: dict) -> dict:
        expected = {**journal["context_hashes"], **{item["path"]: item["after_hash"] for item in journal["files"]}}
        changed = [relative for relative, expected_hash in expected.items() if file_digest(self.root / relative) != expected_hash]
        total = len(self.segments)
        return {
            "task_dir": str(self.task_dir), "phase": journal["phase"], "segments": total,
            "completed": total if journal["phase"] == "applied" else 0,
            "validated": total, "missing_count": 0, "invalid_count": 0, "error_count": 0,
            "missing": [], "invalid": [], "errors": [], "pending_packets": 0, "next_packets": [],
            "changed_since_merge": changed if journal["phase"] == "applied" else [],
            "next_action": "audit_and_build" if journal["phase"] == "applied" else "resume_merge",
        }

    def finish_merge(self, journal: dict, check_only: bool = False) -> dict:
        if journal["phase"] == "applied":
            if any(file_digest(self.task_dir / relative) != expected for relative, expected in journal["result_hashes"].items()):
                raise ValueError("results changed after merge; use resume to continue without overwriting merged sources")
            if any(file_digest(self.root / item["path"]) != item["after_hash"] for item in journal["files"]):
                raise ValueError("already merged; sources changed afterwards; use resume to continue audit/build, not apply")
            return {"phase": "applied", "already_applied": True, "files": len(journal["files"])}
        errors = []
        changed_paths = {item["path"] for item in journal["files"]}
        for relative, expected in journal["context_hashes"].items():
            if relative not in changed_paths and file_digest(self.root / relative) != expected:
                errors.append(f"merge context changed: {relative}")
        for relative, expected in journal["result_hashes"].items():
            if file_digest(self.task_dir / relative) != expected:
                errors.append(f"results changed during interrupted merge: {relative}")
        for relative, expected in journal.get("packet_hashes", {}).items():
            if file_digest(self.task_dir / relative) != expected:
                errors.append(f"packet changed during interrupted merge: {relative}")
        for item in journal["files"]:
            if file_digest(self.root / item["path"]) not in (item["before_hash"], item["after_hash"]):
                errors.append(f"source changed during interrupted merge: {item['path']}")
            if file_digest(self.task_dir / item["staged"]) != item["after_hash"]:
                errors.append(f"staged merge output changed: {item['path']}")
        if errors:
            raise ValueError("; ".join(errors))
        if check_only:
            return {"phase": "applying", "validated": len(self.segments), "check_only": True}
        for item in journal["files"]:
            path = self.root / item["path"]
            if file_digest(path) != item["after_hash"]:
                tasks._write_atomic(path, (self.task_dir / item["staged"]).read_text(encoding="utf-8"))
        journal["phase"] = "applied"
        write_json(self.journal_path, journal)
        return {"phase": "applied", "files": len(journal["files"]), "segments": len(self.segments)}

    def merge(self, check_only: bool = False) -> dict:
        existing = self.journal()
        if existing:
            return self.finish_merge(existing, check_only)
        inspections = [(packet, self.inspect(packet, persist=not check_only)) for packet in self.packets]
        errors = []
        results = {}
        for packet, result in inspections:
            errors.extend(result["blocked"] + result["file_errors"])
            errors.extend(f"{key}: {error}" for key, items in result["issues"].items() for error in items)
            errors.extend(f"missing segment {key}" for key in result["missing"])
            results.update(result["valid"])
        if errors:
            return {"phase": "needs_repair", "errors": list(dict.fromkeys(errors)), "error_count": len(errors)}
        if check_only:
            return {"phase": "ready", "validated": len(results), "check_only": True}
        replacements = defaultdict(list)
        for segment_id, translation in results.items():
            segment = self.segments[segment_id]
            translation = translation.lstrip("\n")
            translation = translation.rstrip("\n") + ("\n" if segment["source"].endswith("\n") else "")
            restored = tasks._restore(translation, segment["protected"])
            replacements[segment["path"]].append((segment["start_line"], segment["end_line"], restored))
        files = []
        for number, (relative, items) in enumerate(sorted(replacements.items())):
            path = self.root / relative
            before = path.read_bytes()
            if digest(before) != self.source_hashes[relative]:
                raise ValueError(f"source changed while staging merge: {relative}")
            lines = before.decode("utf-8").splitlines(keepends=True)
            for start, end, translation in sorted(items, reverse=True):
                lines[start - 1:end] = [translation]
            after = "".join(lines)
            staged = f".merge/staged-{number:04d}"
            tasks._write_atomic(self.task_dir / staged, after)
            files.append({"path": relative, "before_hash": digest(before), "after_hash": digest(after.encode()), "staged": staged})
        journal = {
            "version": 1, "manifest_hash": self.key, "phase": "applying", "files": files,
            "context_hashes": self.source_hashes,
            "result_hashes": {str(self.result_path(packet).relative_to(self.task_dir)): result["result_hash"] for packet, result in inspections},
            "packet_hashes": {packet["path"]: packet["sha256"] for packet in self.packets if "sha256" in packet and self.manifest["version"] != 1},
        }
        write_json(self.journal_path, journal)
        return self.finish_merge(journal)


def emit(payload: dict, args, workers: int) -> None:
    public = dict(payload)
    if isinstance(public.get("segments"), list):
        public["segment_count"] = len(public["segments"])
    if not args.details:
        for key in ("missing", "invalid", "errors", "changed_since_merge"):
            if key in public:
                public[key] = public[key][:5]
        if "errors" in public:
            public["errors"] = [error[:300] for error in public["errors"]]
        if isinstance(public.get("segments"), list):
            public["segments"] = public["segments"][:5]
        if "next_packets" in public:
            public["next_packets"] = public["next_packets"][:workers]
    if getattr(args, "json", False):
        print(json.dumps(public, ensure_ascii=False))
        return
    print(" ".join(f"{key}={public[key]}" for key in ("phase", "validated", "segments", "segment_count", "pending_packets", "error_count", "repaired", "preserved", "already_applied") if key in public))
    tasks._print_errors(payload.get("errors", []), args.details)
    for item in public.get("next_packets", []):
        print(f"next: {item['action']} {item['path']} -> {item['result_path']}")
        if "repair_path" in item:
            print(f"repair: {item['repair_path']} -> {item['repair_result_path']}")
    if "path" in public:
        print(f"repair: {public['path']} -> {public['result_path']}")
    if "next_action" in public:
        print(f"next_action={public['next_action']}")


def dispatch(args) -> int:
    try:
        progress = Progress(args.root, args.output)
        journal = progress.journal()
        if args.command == "apply":
            payload = progress.merge(args.check)
        elif args.command == "repair":
            if journal:
                raise ValueError("translation merge has started; finish it before editing results")
            payload = progress.repair(progress.packet(args.packet), args.apply)
        elif journal:
            if args.command == "resume" and journal["phase"] == "applying":
                progress.finish_merge(journal)
            payload = progress.merged_report(progress.journal())
        else:
            packets = [progress.packet(args.packet)] if args.command == "check" else progress.packets
            payload = progress.report(packets, persist=args.command in ("check", "resume"))
            if args.command == "check":
                if payload["phase"] != "blocked":
                    payload["next_action"] = "repair" if payload["pending_packets"] else "continue_queue"
                if not payload["pending_packets"]:
                    payload["phase"] = "packet_validated"
        emit(payload, args, int(progress.manifest["metrics"]["workers"]))
        if payload.get("error_count"):
            return 1
        if args.command in ("status", "check") and payload.get("pending_packets"):
            return 1
        return 0
    except (OSError, ValueError) as error:
        payload = {"phase": "blocked", "error_count": 1, "errors": [str(error)]}
        emit(payload, args, 1)
        return 1
