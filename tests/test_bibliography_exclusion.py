from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "arxiv-paper-zh" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tex_translation_utils import mask_bibliography  # noqa: E402


class BibliographyExclusionTests(unittest.TestCase):
    def test_masks_reference_section_but_not_following_appendix(self) -> None:
        source = r"""\section{Results}
Visible result prose should remain here.
\section*{References}
Reference Author Long English Title.
\section{Appendix}
Appendix prose should remain visible here.
"""
        masked = mask_bibliography(source)
        self.assertIn("Visible result prose should remain here.", masked)
        self.assertNotIn("Reference Author Long English Title.", masked)
        self.assertIn("Appendix prose should remain visible here.", masked)

    def test_inventory_skips_reference_files_and_embedded_reference_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text(
                r"""\input{body}
\begin{thebibliography}{9}
\input{citations}
\end{thebibliography}
\input{references}
""",
                encoding="utf-8",
            )
            (root / "body.tex").write_text(
                "This body contains enough English prose for translation.\n",
                encoding="utf-8",
            )
            (root / "citations.tex").write_text(
                "Citation Author and English Paper Title.\n",
                encoding="utf-8",
            )
            (root / "references.tex").write_text(
                "Another Author and English Reference Title.\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "inventory_and_shard.py"),
                    str(root),
                    "--entry",
                    "main.tex",
                    "--workers",
                    "1",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            files = json.loads(result.stdout)[0]["files"]
            self.assertEqual(files, ["body.tex"])

    def test_audit_ignores_bibliography_and_flags_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "paper.tex").write_text(
                r"""This untranslated body sentence needs attention.
\begin{thebibliography}{9}
\bibitem{key} Reference Author, A Long English Paper Title.
\end{thebibliography}
""",
                encoding="utf-8",
            )
            (root / "refs.tex").write_text(
                "Reference Author and Another English Paper Title.\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "audit_tex_translation.py"),
                    str(root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("paper.tex:1:", result.stdout)
            self.assertNotIn("Reference Author", result.stdout)
            self.assertNotIn("refs.tex", result.stdout)
            self.assertIn("suspect_lines=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
