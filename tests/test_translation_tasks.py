from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "arxiv-paper-zh" / "scripts" / "translation_tasks.py"


def run_tasks(*arguments: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
        check=check,
        capture_output=True,
        text=True,
    )


def fill_packets(task_dir: Path, transform) -> dict[str, object]:
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    segments = {segment["id"]: segment for segment in manifest["segments"]}
    for packet in manifest["packets"]:
        path = task_dir / packet["result_path"]
        rows = [
            json.dumps({"id": segment_id, "translation": transform(segments[segment_id]["packet_source"])}, ensure_ascii=False)
            for segment_id in packet["segments"]
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest


class TranslationTaskTests(unittest.TestCase):
    SOURCE = r"""\documentclass{article}
\title{Fast Paper Translation}
\begin{document}
\section{Introduction}
This method improves the result in $E = mc^2$ by 20\% \cite{paper}.
% This long implementation note should never consume translation tokens.
\begin{equation}
very_long_equation_name = alpha + beta + gamma + delta + epsilon
\end{equation}
\section*{References}
\bibitem{paper} Reference Author, A Long English Paper Title.
\end{document}
"""

    @staticmethod
    def translate(source: str) -> str:
        return (
            source.replace("Fast Paper Translation", "快速论文翻译")
            .replace("Introduction", "引言")
            .replace("This method improves the result in", "该方法提升了结果")
            .replace("by", "幅度为")
        )

    def test_compact_packets_include_entry_and_apply_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "main.tex"
            paper.write_text(self.SOURCE, encoding="utf-8")

            result = run_tasks(
                "prepare",
                root,
                "--entry",
                "main.tex",
                "--workers",
                3,
                "--json",
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["workers"], 1)
            self.assertLess(summary["packet_source_bytes"], summary["scanned_bytes"])

            task_dir = root / ".translation-tasks"
            packet_path = task_dir / summary["packets"][0]["path"]
            packet_text = packet_path.read_text(encoding="utf-8")
            self.assertEqual(summary["packet_bytes"], len(packet_text.encode()))
            self.assertEqual(summary["input_byte_reduction_percent"], round(100 * (1 - summary["packet_bytes"] / summary["scanned_bytes"]), 1))
            self.assertIn("⟪T", packet_text)
            self.assertNotIn("very_long_equation_name", packet_text)
            self.assertNotIn("implementation note", packet_text)
            self.assertNotIn("Reference Author", packet_text)
            self.assertNotIn("@@@ TRANSLATION", packet_text)
            initial = json.loads(run_tasks("status", root, "--json", check=False).stdout)
            self.assertEqual(initial["completed"], 0)
            self.assertEqual(initial["error_count"], 0)

            manifest = fill_packets(task_dir, self.translate)
            self.assertTrue(any(segment["path"] == "main.tex" for segment in manifest["segments"]))
            self.assertEqual(packet_path.read_text(encoding="utf-8"), packet_text)
            result_path = task_dir / manifest["packets"][0]["result_path"]
            rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(set(row) == {"id", "translation"} for row in rows))
            self.assertNotIn("Fast Paper Translation", result_path.read_text(encoding="utf-8"))
            status = json.loads(run_tasks("status", root, "--json").stdout)
            self.assertEqual(status["completed"], status["segments"])
            self.assertEqual(status["next_packets"], [])
            run_tasks("apply", root)

            translated = paper.read_text(encoding="utf-8")
            self.assertIn(r"\title{快速论文翻译}", translated)
            self.assertIn(r"\section{引言}", translated)
            self.assertIn(r"$E = mc^2$", translated)
            self.assertIn(r"\cite{paper}", translated)
            self.assertIn("very_long_equation_name = alpha + beta", translated)
            self.assertIn("Reference Author, A Long English Paper Title.", translated)

    def test_apply_rejects_missing_placeholder_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "main.tex"
            paper.write_text(self.SOURCE, encoding="utf-8")
            run_tasks("prepare", root, "--entry", "main.tex")
            fill_packets(root / ".translation-tasks", lambda source: source.replace("⟪T0000⟫", ""))

            result = run_tasks("apply", root, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("placeholder mismatch", result.stdout)
            self.assertEqual(paper.read_text(encoding="utf-8"), self.SOURCE)

    def test_long_single_file_has_bounded_packets_independent_of_workers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paragraph = (
                "This paragraph contains enough visible English prose to form "
                "an independent translation task for testing.\n\n"
            )
            (root / "main.tex").write_text(
                "\\documentclass{article}\n\\begin{document}\n"
                + paragraph * 9
                + "\\end{document}\n",
                encoding="utf-8",
            )
            result = run_tasks(
                "prepare",
                root,
                "--entry",
                "main.tex",
                "--workers",
                3,
                "--chunk-words",
                20,
                "--packet-words",
                40,
                "--min-words-per-worker",
                1,
                "--json",
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["workers"], 3)
            self.assertGreater(summary["packet_count"], summary["workers"])
            self.assertEqual(len(summary["packets"]), summary["workers"])

            manifest = json.loads(
                (root / ".translation-tasks" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(
                all(segment["path"] == "main.tex" for segment in manifest["segments"])
            )
            self.assertTrue(all(packet["weight"] <= 40 for packet in manifest["packets"]))
            ids = [segment_id for packet in manifest["packets"] for segment_id in packet["segments"]]
            self.assertEqual(ids, [segment["id"] for segment in manifest["segments"]])
            initial = json.loads(run_tasks("status", root, "--json", check=False).stdout)
            self.assertEqual(len(initial["next_packets"]), 3)
            self.assertGreater(initial["missing_count"], len(initial["missing"]))
            all_pending = json.loads(run_tasks("status", root, "--json", "--details", check=False).stdout)
            self.assertEqual(len(all_pending["next_packets"]), len(manifest["packets"]))

            # Finishing one batch exposes the next batch without re-emitting source text.
            fill_packets(root / ".translation-tasks", lambda source: source.replace("This paragraph", "这一段"))
            for packet in manifest["packets"][1:]:
                (root / ".translation-tasks" / packet["result_path"]).unlink()
            progress = json.loads(run_tasks("status", root, "--json", check=False).stdout)
            self.assertEqual(progress["next_packets"][0]["path"], manifest["packets"][1]["path"])
            self.assertNotIn("visible English prose", json.dumps(progress))

    def test_oversized_segment_fails_before_creating_packets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text("word " * 51, encoding="utf-8")
            result = run_tasks("prepare", root, "--packet-words", 50, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exceeding --packet-words 50", result.stderr)
            self.assertFalse((root / ".translation-tasks").exists())

    def test_packet_cap_applies_even_when_chunk_target_is_larger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(("word " * 20 + "\n") * 4, encoding="utf-8")
            summary = json.loads(run_tasks("prepare", root, "--packet-words", 40, "--json", "--details").stdout)
            self.assertEqual(summary["packet_count"], 2)
            self.assertTrue(all(packet["words"] <= 40 for packet in summary["packets"]))

    def test_default_packet_budget_bounds_a_large_paper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(("word " * 100 + "\n\n") * 100, encoding="utf-8")
            summary = json.loads(run_tasks("prepare", root, "--json").stdout)
            self.assertEqual(summary["packet_words"], 2000)
            self.assertEqual(summary["workers"], 3)
            self.assertGreater(summary["packet_count"], 3)
            manifest = json.loads((root / ".translation-tasks" / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(all(packet["weight"] <= 2000 for packet in manifest["packets"]))
            fill_packets(root / ".translation-tasks", lambda source: source.replace("word", "词语"))
            run_tasks("apply", root)
            self.assertNotIn("word", (root / "main.tex").read_text(encoding="utf-8"))

    def test_malformed_or_unowned_results_never_modify_source(self) -> None:
        transforms = {
            "invalid JSONL": lambda rows: "not json\n",
            "expected only string fields": lambda rows: json.dumps({**rows[0], "source": "repeated English"}) + "\n",
            "expected only string fields (type)": lambda rows: json.dumps({"id": rows[0]["id"], "translation": 42}) + "\n",
            "duplicate segment": lambda rows: "\n".join(json.dumps(row) for row in rows + rows[:1]) + "\n",
            "unexpected segment": lambda rows: json.dumps({"id": "unknown", "translation": "译文"}) + "\n",
        }
        for label, transform in transforms.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paper = root / "main.tex"
                paper.write_text(self.SOURCE, encoding="utf-8")
                run_tasks("prepare", root)
                task_dir = root / ".translation-tasks"
                manifest = fill_packets(task_dir, self.translate)
                result_path = task_dir / manifest["packets"][0]["result_path"]
                rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines()]
                result_path.write_text(transform(rows), encoding="utf-8")
                result = run_tasks("apply", root, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(label.split(" (")[0], result.stdout)
                self.assertEqual(paper.read_text(encoding="utf-8"), self.SOURCE)

    def test_read_only_input_and_source_snapshot_are_checked(self) -> None:
        for target in ("packet", "source"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paper = root / "main.tex"
                paper.write_text(self.SOURCE, encoding="utf-8")
                run_tasks("prepare", root)
                task_dir = root / ".translation-tasks"
                manifest = fill_packets(task_dir, self.translate)
                path = task_dir / manifest["packets"][0]["path"] if target == "packet" else paper
                path.write_text(path.read_text(encoding="utf-8").replace("Fast Paper", "Changed Paper"), encoding="utf-8")
                before = paper.read_bytes()
                result = run_tasks("apply", root, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("packet was modified" if target == "packet" else "source changed", result.stdout)
                self.assertEqual(paper.read_bytes(), before)

    def test_legacy_version_one_results_can_still_be_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "main.tex"
            paper.write_text(self.SOURCE, encoding="utf-8")
            run_tasks("prepare", root)
            task_dir = root / ".translation-tasks"
            manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
            manifest["version"] = 1
            packet = manifest["packets"][0]
            packet["path"] = "worker-01.task"
            packet.pop("result_path")
            packet.pop("sha256")
            text = "".join(
                f"@@@ SEGMENT {segment['id']}\n@@@ SOURCE\n{segment['packet_source']}"
                f"@@@ TRANSLATION\n{self.translate(segment['packet_source'])}@@@ END\n"
                for segment in manifest["segments"]
            )
            (task_dir / packet["path"]).write_text(text, encoding="utf-8")
            (task_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            run_tasks("status", root)
            run_tasks("apply", root)
            self.assertIn("快速论文翻译", paper.read_text(encoding="utf-8"))

    def test_force_reprepare_removes_stale_results_after_validating_new_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "main.tex"
            paper.write_text(self.SOURCE, encoding="utf-8")
            run_tasks("prepare", root)
            task_dir = root / ".translation-tasks"
            manifest = fill_packets(task_dir, self.translate)
            result_path = task_dir / manifest["packets"][0]["result_path"]
            result_before = result_path.read_bytes()
            paper.write_text("word " * 51, encoding="utf-8")
            failed = run_tasks("prepare", root, "--packet-words", 50, "--force", check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(result_path.read_bytes(), result_before)
            paper.write_text(self.SOURCE, encoding="utf-8")
            run_tasks("prepare", root, "--force")
            self.assertFalse(result_path.exists())
            status = json.loads(run_tasks("status", root, "--json", check=False).stdout)
            self.assertEqual(status["completed"], 0)

    def test_packet_budget_must_be_positive(self) -> None:
        result = run_tasks("prepare", ".", "--packet-words", 0, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--packet-words must be positive", result.stderr)


if __name__ == "__main__":
    unittest.main()
