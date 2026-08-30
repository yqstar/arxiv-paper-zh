from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "arxiv-paper-zh" / "scripts"
PREPARE = SCRIPTS / "prepare_output_layout.py"
FINALIZE = SCRIPTS / "finalize_output.py"


def run(script: Path, *arguments: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(argument) for argument in arguments)],
        check=check,
        capture_output=True,
        text=True,
    )


class OutputLayoutTests(unittest.TestCase):
    def populate_deliverables(self, paper_root: Path) -> None:
        (paper_root / "latex" / "source.tar").write_bytes(b"source archive")
        (paper_root / "latex" / "paper-en" / "main.tex").write_text(
            "English source", encoding="utf-8"
        )
        (paper_root / "latex" / "paper-zh" / "main.tex").write_text(
            "Chinese source", encoding="utf-8"
        )
        (paper_root / "paper-en" / f"{paper_root.name}-en.pdf").write_bytes(b"%PDF-en")
        (paper_root / "paper-zh" / f"{paper_root.name}-zh.pdf").write_bytes(b"%PDF-zh")

    def test_prepare_exposes_canonical_archive_and_managed_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run(PREPARE, "EST", "--root", directory)
            paths = json.loads(result.stdout)
            paper_root = Path(paths["paper_root"])

            self.assertEqual(Path(paths["source_archive"]), paper_root / "latex" / "source.tar")
            self.assertEqual(Path(paths["temp_root"]), paper_root / "tmp")
            self.assertTrue(Path(paths["temp_root"]).is_dir())
            self.assertFalse((paper_root / "source").exists())
            self.assertFalse((paper_root / "latex" / "source").exists())

    def test_finalize_validates_deliverables_then_removes_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = json.loads(run(PREPARE, "EST", "--root", directory).stdout)
            paper_root = Path(paths["paper_root"])
            self.populate_deliverables(paper_root)
            (paper_root / "tmp" / "render-zh").mkdir(parents=True)
            (paper_root / "tmp" / "render-zh" / "page-1.png").write_bytes(b"png")

            result = run(FINALIZE, paper_root)
            summary = json.loads(result.stdout)

            self.assertEqual(summary["status"], "complete")
            self.assertTrue(summary["removed_tmp"])
            self.assertFalse((paper_root / "tmp").exists())
            self.assertTrue((paper_root / "latex" / "source.tar").is_file())

    def test_finalize_preserves_tmp_when_source_archive_is_misplaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = json.loads(run(PREPARE, "EST", "--root", directory).stdout)
            paper_root = Path(paths["paper_root"])
            self.populate_deliverables(paper_root)
            (paper_root / "source").mkdir()
            (paper_root / "source" / "main.tex").write_text("staging", encoding="utf-8")

            result = run(FINALIZE, paper_root, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected source staging path", result.stdout)
            self.assertIn("tmp_preserved=true", result.stdout)
            self.assertTrue((paper_root / "tmp").is_dir())


if __name__ == "__main__":
    unittest.main()
