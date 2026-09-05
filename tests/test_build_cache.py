from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_cli_output import SCRIPTS, make_tool


class BuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "latex" / "paper-zh"
        self.source.mkdir(parents=True)
        self.entry = self.source / "main.tex"
        self.entry.write_text("A paper")
        self.bin = self.root / "bin"
        self.dep = self.root / "external.sty"
        self.dep.write_text("style v1")
        make_tool(self.bin / "xelatex", r'''
            import hashlib, json
            from pathlib import Path
            here = Path(__file__).parent
            calls = here / "calls.json"
            history = json.loads(calls.read_text()) if calls.exists() else []
            history.append(Path(__file__).name)
            calls.write_text(json.dumps(history))
            entry = Path(sys.argv[-1])
            source = entry.read_text()
            options = json.loads((here / "options.json").read_text()) if (here / "options.json").exists() else {}
            if options.get("fail"):
                print("! deliberate compilation failure")
                sys.exit(1)
            aux = hashlib.sha256(source.encode()).hexdigest() + "\n"
            outputs = [entry.with_suffix(".aux"), entry.with_suffix(".log"), entry.with_suffix(".pdf")]
            if options.get("bibliography") == "bibtex":
                aux += "\\@input{section.aux}\n"
                Path("section.aux").write_text("\\citation{key}\n\\bibstyle{plain}\n\\bibdata{refs}\n")
                outputs.append(Path("section.aux"))
            if options.get("bibliography") == "biber":
                entry.with_suffix(".bcf").write_text('<control><datasource>refs.bib</datasource></control>')
                outputs.append(entry.with_suffix(".bcf"))
            if options.get("unstable"):
                aux += str(len(history))
            entry.with_suffix(".aux").write_text(aux)
            entry.with_suffix(".log").write_text(options.get("log", "Build clean\n"))
            entry.with_suffix(".pdf").write_text("%PDF-test " + source)
            inputs = [entry, here.parent / "external.sty"]
            entry.with_suffix(".fls").write_text("".join(f"INPUT {path.resolve()}\n" for path in inputs) + "".join(f"OUTPUT {path.resolve()}\n" for path in outputs))
        ''')
        for name in ("bibtex", "biber"):
            make_tool(self.bin / name, r'''
                import json
                from pathlib import Path
                here = Path(__file__).parent
                calls = here / "calls.json"
                history = json.loads(calls.read_text())
                history.append(Path(__file__).name)
                calls.write_text(json.dumps(history))
                Path(sys.argv[-1]).with_suffix(".bbl").write_text("references " + Path("refs.bib").read_text())
            ''')

    def configure(self, **options):
        (self.bin / "options.json").write_text(json.dumps(options))

    def invoke(self, *args, ok=True, env=None):
        result = subprocess.run([sys.executable, str(SCRIPTS / "build_and_check.py"), str(self.entry),
                                 "--tex-bin", str(self.bin), "--json", *args],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def bib_inputs(self):
        (self.source / "refs.bib").write_text("bib v1")
        (self.source / "plain.bst").write_text("bst v1")

    def test_convergence_and_verified_reuse(self):
        first = self.invoke()
        self.assertEqual(first["runs"], 2)
        log = Path(first["build_log"]).read_bytes()
        cached = self.invoke()
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["runs"], 0)
        self.assertEqual(Path(cached["build_log"]).read_bytes(), log)
        self.assertEqual(len(json.loads((self.bin / "calls.json").read_text())), 2)

    def test_local_sources_assets_and_external_dependencies_invalidate(self):
        self.invoke()
        for path, content in ((self.entry, "Revised paper"), (self.source / "figure.pdf", "%PDF-figure"),
                              (self.source / "figure.pdf", "%PDF-changed"), (self.dep, "style v2")):
            with self.subTest(path=path, content=content):
                path.write_text(content)
                self.assertFalse(self.invoke()["cached"])
                self.assertTrue(self.invoke()["cached"])
        self.dep.unlink()
        self.assertFalse(self.invoke()["cached"])

    def test_changed_or_missing_artifacts_rebuild(self):
        self.invoke()
        for suffix in (".pdf", ".aux", ".log"):
            with self.subTest(suffix=suffix):
                self.entry.with_suffix(suffix).write_text("corrupt")
                self.assertFalse(self.invoke()["cached"])
                self.entry.with_suffix(suffix).unlink()
                self.assertFalse(self.invoke()["cached"])

    def test_runtime_environment_engine_alias_and_force_invalidate(self):
        self.invoke()
        self.assertFalse(self.invoke(env={**os.environ, "TEXINPUTS": str(self.root) + ":"})["cached"])
        self.invoke()
        self.assertFalse(self.invoke("--force")["cached"])
        engine = self.bin / "xelatex"
        engine.write_text(engine.read_text() + "\n# runtime changed\n")
        self.assertFalse(self.invoke()["cached"])
        (self.bin / "pdflatex").symlink_to(engine)
        self.assertFalse(self.invoke("--engine", "pdflatex")["cached"])

    def test_unstable_references_and_fatal_logs_never_cache(self):
        for options in ({"unstable": True}, {"log": "LaTeX Warning: There were undefined references.\n"},
                        {"log": "Package rerunfilecheck Warning: Rerun to get outlines right\n"}):
            with self.subTest(options=options):
                self.configure(**options)
                result = self.invoke("--max-runs", "3", ok=False)
                self.assertEqual(result["runs"], 3)
                self.assertFalse(list((self.root / "tmp" / ".build-cache").glob("*.json")))
        self.configure(log="Missing character: 中\n")
        self.assertEqual(self.invoke(ok=False)["runs"], 1)

    def test_failure_json_and_corrupt_cache_recovery(self):
        self.configure(fail=True)
        self.assertFalse(self.invoke(ok=False)["ok"])
        self.configure()
        self.invoke()
        cache = next((self.root / "tmp" / ".build-cache").glob("*.json"))
        for value in ("invalid JSON", '{"generated_outputs": 12}', '{"bibliography": []}'):
            cache.write_text(value)
            self.assertFalse(self.invoke()["cached"])

    def test_nested_bibtex_and_biber_only_run_when_needed(self):
        self.bib_inputs()
        for kind in ("bibtex", "biber"):
            with self.subTest(kind=kind):
                self.configure(bibliography=kind)
                first = self.invoke("--force")
                self.assertEqual(first["bibliography_runs"], 1)
                self.assertGreaterEqual(first["runs"], 2)
                self.assertTrue(self.invoke()["cached"])
                self.entry.write_text(self.entry.read_text() + " Revised paragraph.")
                self.assertEqual(self.invoke()["bibliography_runs"], 0)
                (self.source / "refs.bib").write_text("bib changed " + kind)
                self.assertEqual(self.invoke()["bibliography_runs"], 1)
                self.entry.with_suffix(".bbl").unlink()
                self.assertEqual(self.invoke()["bibliography_runs"], 1)
                if kind == "bibtex":
                    (self.source / "plain.bst").write_text("bst changed")
                    self.assertEqual(self.invoke()["bibliography_runs"], 1)

    def test_bibliography_without_output_fails(self):
        self.bib_inputs()
        self.configure(bibliography="bibtex")
        make_tool(self.bin / "bibtex", "pass\n")
        result = self.invoke(ok=False)
        self.assertIn("missing bibliography output", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
