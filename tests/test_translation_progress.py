from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_translation_tasks import fill_packets, run_tasks


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "arxiv-paper-zh" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import translation_progress as progress
import translation_tasks as tasks


SOURCE = r"""\documentclass{article}
\begin{document}
\section{Introduction}
This first method uses $x$ and improves the training results.

This second method uses $y$ and improves the evaluation results.

This third method uses $z$ and improves the final results.
\end{document}
"""


def translate(source: str) -> str:
    return source.replace("Introduction", "引言").replace("This first method", "第一种方法").replace("This second method", "第二种方法").replace("This third method", "第三种方法")


class ProgressTests(unittest.TestCase):
    def prepare(self, root: Path, packet_words: int = 2000) -> tuple[Path, dict]:
        (root / "main.tex").write_text(SOURCE, encoding="utf-8")
        run_tasks("prepare", root, "--entry", "main.tex", "--chunk-words", 8, "--packet-words", packet_words)
        task_dir = root / ".translation-tasks"
        manifest = fill_packets(task_dir, translate)
        self.assertGreaterEqual(len(manifest["segments"]), 3)
        return task_dir, manifest

    def damage_one(self, task_dir: Path, manifest: dict) -> tuple[str, list[dict]]:
        packet = manifest["packets"][0]
        result_path = task_dir / packet["result_path"]
        rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines()]
        rows[0]["translation"] = rows[0]["translation"].replace("⟪T0000⟫", "")
        result_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return rows[0]["id"], rows

    def fill_repair(self, task_dir: Path, manifest: dict, repair: dict) -> None:
        segments = {segment["id"]: segment for segment in manifest["segments"]}
        rows = [{"id": segment_id, "translation": translate(segments[segment_id]["packet_source"])} for segment_id in repair["segments"]]
        (task_dir / repair["result_path"]).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def test_check_one_packet_does_not_require_other_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root, packet_words=15)
            self.assertGreater(len(manifest["packets"]), 1)
            for packet in manifest["packets"][1:]:
                (task_dir / packet["result_path"]).unlink()
            packet = manifest["packets"][0]
            checked = json.loads(run_tasks("check", root, "--packet", packet["path"], "--json").stdout)
            self.assertEqual(checked["phase"], "packet_validated")
            self.assertEqual(checked["validated"], len(packet["segments"]))
            self.assertEqual(checked["next_action"], "continue_queue")
            resumed = json.loads(run_tasks("resume", root, "--json").stdout)
            self.assertEqual(resumed["completed"], checked["validated"])
            self.assertEqual(resumed["next_packets"][0]["path"], manifest["packets"][1]["path"])

    def test_unchanged_validated_packet_uses_checkpoint_but_changed_result_is_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root)
            packet = manifest["packets"][0]
            run_tasks("check", root, "--packet", packet["path"])
            state = progress.Progress(root, Path(tasks.DEFAULT_TASK_DIR))
            with patch.object(progress, "translation_errors", side_effect=AssertionError("unexpected repeated validation")):
                cached = state.inspect(packet)
                self.assertEqual(len(cached["valid"]), len(packet["segments"]))
            bad_id, _ = self.damage_one(task_dir, manifest)
            result = json.loads(run_tasks("check", root, "--packet", packet["path"], "--json", check=False).stdout)
            self.assertEqual(result["invalid"], [bad_id])
            self.assertIn("placeholder mismatch", " ".join(result["errors"]))

    def test_repair_contains_only_bad_segment_and_preserves_all_good_translations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root)
            packet = manifest["packets"][0]
            bad_id, rows = self.damage_one(task_dir, manifest)
            repair = json.loads(run_tasks("repair", root, "--packet", packet["path"], "--json").stdout)
            self.assertEqual(repair["segments"], [bad_id])
            text = (task_dir / repair["path"]).read_text(encoding="utf-8")
            self.assertIn("@@@ CURRENT", text)
            self.assertIn("placeholder mismatch", text)
            for row in rows[1:]:
                self.assertNotIn(row["id"], text)
            self.fill_repair(task_dir, manifest, repair)
            before = (task_dir / repair["result_path"]).read_bytes()
            repeated = json.loads(run_tasks("repair", root, "--packet", packet["path"], "--json").stdout)
            self.assertEqual(repeated["path"], repair["path"])
            self.assertEqual((task_dir / repair["result_path"]).read_bytes(), before)
            resumed = json.loads(run_tasks("resume", root, "--json", check=False).stdout)
            self.assertEqual(resumed["next_packets"][0]["action"], "repair-apply")
            run_tasks("repair", root, "--packet", packet["path"], "--apply")
            corrected = [json.loads(line) for line in (task_dir / packet["result_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(corrected[1:], rows[1:])
            run_tasks("repair", root, "--packet", packet["path"], "--apply")
            run_tasks("check", root, "--packet", packet["path"])
            run_tasks("apply", root)
            self.assertIn("第一种方法", (root / "main.tex").read_text(encoding="utf-8"))

    def test_partial_or_malformed_result_repairs_only_affected_rows(self) -> None:
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task_dir, manifest = self.prepare(root)
                packet = manifest["packets"][0]
                result_path = task_dir / packet["result_path"]
                lines = result_path.read_text(encoding="utf-8").splitlines()
                bad_id = json.loads(lines[0])["id"]
                lines[0] = f'{{"id":"{bad_id}","translation":"bad JSON' if malformed else ""
                result_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                repair = json.loads(run_tasks("repair", root, "--packet", packet["path"], "--json").stdout)
                self.assertEqual(repair["segments"], [bad_id])
                self.fill_repair(task_dir, manifest, repair)
                run_tasks("repair", root, "--packet", packet["path"], "--apply")
                run_tasks("check", root, "--packet", packet["path"])

    def test_repair_rejects_stale_base_and_invalid_correction_without_overwriting(self) -> None:
        for change in ("base", "source", "repair_packet", "bad_correction", "extra_id"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task_dir, manifest = self.prepare(root)
                packet = manifest["packets"][0]
                self.damage_one(task_dir, manifest)
                repair = json.loads(run_tasks("repair", root, "--packet", packet["path"], "--json").stdout)
                self.fill_repair(task_dir, manifest, repair)
                result_path = task_dir / packet["result_path"]
                if change == "base":
                    result_path.write_text(result_path.read_text() + "\n")
                elif change == "source":
                    (root / "main.tex").write_text(SOURCE.replace("article", "report"))
                elif change == "repair_packet":
                    path = task_dir / repair["path"]
                    path.write_text(path.read_text() + "changed\n")
                else:
                    path = task_dir / repair["result_path"]
                    if change == "bad_correction":
                        path.write_text(path.read_text().replace("⟪T0000⟫", ""))
                    else:
                        path.write_text(path.read_text() + json.dumps({"id": "unknown", "translation": "未知"}) + "\n")
                before = result_path.read_bytes()
                result = run_tasks("repair", root, "--packet", packet["path"], "--apply", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result_path.read_bytes(), before)

    def test_unknown_extra_rows_are_removed_without_retranslating_valid_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root)
            packet = manifest["packets"][0]
            path = task_dir / packet["result_path"]
            before = path.read_text(encoding="utf-8")
            path.write_text(before + '{"id":"unknown","translation":"extra"}\n')
            repair = json.loads(run_tasks("repair", root, "--packet", packet["path"], "--json").stdout)
            self.assertEqual(repair["repaired"], 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(len(list((task_dir / ".repairs").glob("*.original"))), 1)

    def test_prepare_resume_preserves_packets_and_reports_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root)
            files = {path: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}
            ready = json.loads(run_tasks("prepare", root, "--resume", "--json").stdout)
            self.assertEqual(ready["phase"], "ready")
            for path, value in files.items():
                self.assertEqual(path.read_bytes(), value)
            (root / "main.tex").write_text(SOURCE.replace("article", "report"))
            changed = run_tasks("resume", root, "--json", check=False)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("source changed", changed.stdout)
            self.assertEqual(json.loads(changed.stdout)["phase"], "blocked")

    def test_repeated_apply_is_idempotent_and_resume_keeps_later_source_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            run_tasks("apply", root)
            first = (root / "main.tex").read_bytes()
            repeated = run_tasks("apply", root)
            self.assertIn("already_applied=True", repeated.stdout)
            self.assertEqual((root / "main.tex").read_bytes(), first)
            (root / "main.tex").write_bytes(first + b"\n% later typesetting fix\n")
            resumed = json.loads(run_tasks("resume", root, "--json").stdout)
            self.assertEqual(resumed["phase"], "applied")
            self.assertEqual(resumed["next_action"], "audit_and_build")
            self.assertEqual(resumed["changed_since_merge"], ["main.tex"])
            self.assertNotEqual(run_tasks("apply", root, check=False).returncode, 0)

    def test_interrupted_multifile_merge_resumes_without_reapplying_written_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.tex").write_text("This first method improves the training results.\n")
            (root / "b.tex").write_text("This second method improves the final results.\n")
            run_tasks("prepare", root)
            fill_packets(root / ".translation-tasks", translate)
            state = progress.Progress(root, Path(tasks.DEFAULT_TASK_DIR))
            original_write = tasks._write_atomic

            def interrupt(path: Path, text: str) -> None:
                if path == (root / "b.tex").resolve():
                    raise OSError("simulated process interruption")
                original_write(path, text)

            with patch.object(tasks, "_write_atomic", side_effect=interrupt):
                with self.assertRaises(OSError):
                    state.merge()
            first = (root / "a.tex").read_bytes()
            modified = (root / "a.tex").stat().st_mtime_ns
            self.assertIn("第一种方法", first.decode())
            self.assertIn("This second method", (root / "b.tex").read_text())
            resumed = json.loads(run_tasks("resume", root, "--json").stdout)
            self.assertEqual(resumed["phase"], "applied")
            self.assertEqual((root / "a.tex").read_bytes(), first)
            self.assertEqual((root / "a.tex").stat().st_mtime_ns, modified)
            self.assertIn("第二种方法", (root / "b.tex").read_text())

    def test_conflicting_edit_blocks_interrupted_merge_before_any_more_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            state = progress.Progress(root, Path(tasks.DEFAULT_TASK_DIR))
            original_write = tasks._write_atomic

            def interrupt(path: Path, text: str) -> None:
                if path == (root / "main.tex").resolve():
                    raise OSError("interrupted")
                original_write(path, text)

            with patch.object(tasks, "_write_atomic", side_effect=interrupt):
                with self.assertRaises(OSError):
                    state.merge()
            (root / "main.tex").write_text(SOURCE + "% concurrent edit\n")
            before = (root / "main.tex").read_bytes()
            resumed = run_tasks("resume", root, "--json", check=False)
            self.assertNotEqual(resumed.returncode, 0)
            self.assertIn("source changed during interrupted merge", resumed.stdout)
            self.assertEqual((root / "main.tex").read_bytes(), before)
            self.assertNotEqual(run_tasks("prepare", root, "--force", check=False).returncode, 0)

    def test_version_two_manifest_can_resume_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, manifest = self.prepare(root)
            manifest["version"] = 2
            manifest.pop("source_files")
            (task_dir / "manifest.json").write_text(json.dumps(manifest))
            ready = json.loads(run_tasks("resume", root, "--json").stdout)
            self.assertEqual(ready["phase"], "ready")
            run_tasks("apply", root)
            run_tasks("resume", root)

    def test_result_or_staged_output_changes_block_merge_recovery(self) -> None:
        for change in ("result", "staged", "packet"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                task_dir, manifest = self.prepare(root)
                state = progress.Progress(root, Path(tasks.DEFAULT_TASK_DIR))
                original_write = tasks._write_atomic

                def interrupt(path: Path, text: str) -> None:
                    if path == (root / "main.tex").resolve():
                        raise OSError("interrupted")
                    original_write(path, text)

                with patch.object(tasks, "_write_atomic", side_effect=interrupt):
                    with self.assertRaises(OSError):
                        state.merge()
                if change == "staged":
                    journal = json.loads((task_dir / ".merge" / "journal.json").read_text())
                    path = task_dir / journal["files"][0]["staged"]
                else:
                    packet = manifest["packets"][0]
                    path = task_dir / packet["result_path" if change == "result" else "path"]
                path.write_bytes(path.read_bytes() + b"\nchanged\n")
                before = (root / "main.tex").read_bytes()
                result = run_tasks("resume", root, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / "main.tex").read_bytes(), before)

    def test_interruption_after_last_write_only_updates_journal_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, _ = self.prepare(root)
            state = progress.Progress(root, Path(tasks.DEFAULT_TASK_DIR))
            original_write = tasks._write_atomic
            journal_writes = 0

            def interrupt(path: Path, text: str) -> None:
                nonlocal journal_writes
                if path == state.journal_path:
                    journal_writes += 1
                    if journal_writes == 2:
                        raise OSError("interrupted before recording completion")
                original_write(path, text)

            with patch.object(tasks, "_write_atomic", side_effect=interrupt):
                with self.assertRaises(OSError):
                    state.merge()
            before = (root / "main.tex").read_bytes()
            modified = (root / "main.tex").stat().st_mtime_ns
            run_tasks("resume", root)
            self.assertEqual((root / "main.tex").read_bytes(), before)
            self.assertEqual((root / "main.tex").stat().st_mtime_ns, modified)
            self.assertEqual(json.loads((task_dir / ".merge" / "journal.json").read_text())["phase"], "applied")

    def test_check_only_does_not_start_a_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir, _ = self.prepare(root)
            checked = json.loads(run_tasks("apply", root, "--check", "--json").stdout)
            self.assertTrue(checked["check_only"])
            self.assertFalse((task_dir / ".merge").exists())
            self.assertEqual((root / "main.tex").read_text(), SOURCE)


if __name__ == "__main__":
    unittest.main()
