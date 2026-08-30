from __future__ import annotations

import json
import re
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
        path = task_dir / packet["path"]
        text = path.read_text(encoding="utf-8")
        for segment_id in packet["segments"]:
            translation = transform(segments[segment_id]["packet_source"])
            pattern = re.compile(
                rf"(^@@@ SEGMENT {re.escape(segment_id)}\n"
                rf"^@@@ SOURCE\n.*?^@@@ TRANSLATION\n)(?=^@@@ END)",
                re.MULTILINE | re.DOTALL,
            )
            text, count = pattern.subn(
                lambda match, value=translation: match.group(1) + value,
                text,
                count=1,
            )
            if count != 1:
                raise AssertionError(f"could not fill {segment_id}")
        path.write_text(text, encoding="utf-8")
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
            self.assertGreater(summary["input_byte_reduction_percent"], 20)

            task_dir = root / ".translation-tasks"
            packet_text = (task_dir / "worker-01.task").read_text(encoding="utf-8")
            self.assertIn("⟪T", packet_text)
            self.assertNotIn("very_long_equation_name", packet_text)
            self.assertNotIn("implementation note", packet_text)
            self.assertNotIn("Reference Author", packet_text)

            manifest = fill_packets(task_dir, self.translate)
            self.assertTrue(any(segment["path"] == "main.tex" for segment in manifest["segments"]))
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

    def test_long_single_file_balances_across_workers(self) -> None:
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
                "--min-words-per-worker",
                1,
                "--json",
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["workers"], 3)
            self.assertGreaterEqual(summary["segments"], 3)
            weights = [packet["weight"] for packet in summary["packets"]]
            self.assertLessEqual(max(weights) - min(weights), 20)

            manifest = json.loads(
                (root / ".translation-tasks" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(
                all(segment["path"] == "main.tex" for segment in manifest["segments"])
            )


if __name__ == "__main__":
    unittest.main()
