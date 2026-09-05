from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "arxiv-paper-zh" / "scripts"


def run_script(name: str, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
    )


def make_tool(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.reconfigure(line_buffering=True)\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


class OutputTests(unittest.TestCase):
    def prepare_build(self, root: Path, source: str = "test") -> tuple[Path, Path]:
        entry = root / "latex" / "paper-zh" / "nested" / "main.tex"
        entry.parent.mkdir(parents=True)
        entry.write_text(source, encoding="utf-8")
        bindir = root / "bin"
        make_tool(bindir / "xelatex", r'''
            import sys
            from pathlib import Path
            entry = Path(sys.argv[-1])
            source = entry.read_text()
            for i in range(100):
                print(f"compiler chatter {i}")
            if "FAIL" in source:
                print("! LaTeX Error: requested failure", file=sys.stderr)
                sys.exit(7)
            log = "Missing character: test\n" * 20 if "MISSING" in source else "Build clean\n"
            entry.with_suffix(".log").write_text(log)
            entry.with_suffix(".pdf").write_bytes(b"%PDF-test")
            entry.with_suffix(".aux").write_text("\\bibdata{refs}\n" if "bibliography" in source else "stable\n")
            entry.with_suffix(".fls").write_text(f"INPUT {entry.name}\nOUTPUT {entry.with_suffix('.aux').name}\n")
        ''')
        return entry, bindir

    def test_build_is_quiet_but_full_output_remains_in_managed_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, bindir = self.prepare_build(root)
            result = run_script("build_and_check.py", entry, "--tex-bin", bindir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("build_ok=", result.stdout)
            self.assertNotIn("compiler chatter", result.stdout)
            self.assertLess(len(result.stdout.splitlines()), 4)
            log = root / "tmp" / "paper-zh-build.log"
            self.assertEqual(log.read_text().count("compiler chatter"), 200)
            self.assertFalse(entry.with_suffix(".build.log").exists())

    def test_verbose_build_can_expose_full_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, bindir = self.prepare_build(root)
            log = root / "tmp" / "custom.log"
            result = run_script("build_and_check.py", entry, "--tex-bin", bindir, "--verbose", "--log-file", log)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("compiler chatter"), 200)
            self.assertTrue(log.exists())

    def test_failed_build_reports_a_bounded_tail_and_keeps_full_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, bindir = self.prepare_build(root, "FAIL")
            result = run_script("build_and_check.py", entry, "--tex-bin", bindir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exit_code=7", result.stdout)
            self.assertIn("requested failure", result.stdout)
            self.assertLessEqual(len(result.stdout.splitlines()), 13)
            self.assertEqual((root / "tmp" / "paper-zh-build.log").read_text().count("compiler chatter"), 100)

    def test_final_tex_errors_still_fail_with_bounded_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, bindir = self.prepare_build(root, "MISSING")
            result = run_script("build_and_check.py", entry, "--tex-bin", bindir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build_errors=20", result.stdout)
            self.assertEqual(result.stdout.count("Missing character"), 5)
            self.assertEqual(entry.with_suffix(".log").read_text().count("Missing character"), 20)

    def test_bibliography_failure_is_not_hidden_by_quiet_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, bindir = self.prepare_build(root, r"\bibliography{refs}")
            make_tool(bindir / "bibtex", 'import sys\nprint("bibliography failure", file=sys.stderr)\nsys.exit(3)\n')
            result = run_script("build_and_check.py", entry, "--tex-bin", bindir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build_failed=bibtex", result.stdout)
            self.assertIn("bibliography failure", result.stdout)

    def test_audit_counts_every_hit_and_allows_full_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.tex").write_text("This untranslated sentence needs review.\n" * 25, encoding="utf-8")
            result = run_script("audit_tex_translation.py", root)
            self.assertEqual(result.returncode, 0)
            self.assertIn("suspect_lines=25", result.stdout)
            self.assertIn("more_suspect_lines=15", result.stdout)
            self.assertEqual(result.stdout.count("main.tex:"), 10)
            details = run_script("audit_tex_translation.py", root, "--details")
            self.assertEqual(details.stdout.count("main.tex:"), 25)

    def test_package_install_is_quiet_and_keeps_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "latex" / "paper-zh"
            source.mkdir(parents=True)
            (source / "main.tex").write_text(r"\usepackage{amsmath,xcolor}", encoding="utf-8")
            bindir = root / "bin"
            make_tool(bindir / "kpsewhich", "pass\n")
            make_tool(bindir / "tlmgr", 'print("installer chatter\\n" * 100)\n')
            result = run_script("prepare_tex_runtime.py", source, "--kpsewhich", bindir / "kpsewhich", "--tlmgr", bindir / "tlmgr", "--install")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("checked_packages=2", result.stdout)
            self.assertIn("missing_packages=amsmath xcolor", result.stdout)
            self.assertNotIn("installer chatter", result.stdout)
            logs = list((root / "tmp").glob("tex-install-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].read_text().count("installer chatter"), 100)

    def test_package_install_failure_retains_exit_code_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "latex" / "paper-zh"
            source.mkdir(parents=True)
            (source / "main.tex").write_text(r"\usepackage{amsmath}", encoding="utf-8")
            make_tool(root / "bin" / "kpsewhich", "pass\n")
            make_tool(root / "bin" / "tlmgr", 'import sys\nprint("installer chatter\\n" * 100)\nprint("install failure", file=sys.stderr)\nsys.exit(5)\n')
            result = run_script("prepare_tex_runtime.py", source, "--kpsewhich", root / "bin" / "kpsewhich", "--tlmgr", root / "bin" / "tlmgr", "--install")
            self.assertEqual(result.returncode, 5)
            self.assertIn("install failure", result.stdout)
            self.assertLessEqual(len(result.stdout.splitlines()), 15)


if __name__ == "__main__":
    unittest.main()
