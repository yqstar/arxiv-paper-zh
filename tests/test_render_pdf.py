from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from test_cli_output import make_tool, run_script


class RenderCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-test")
        self.bin = self.root / "bin"
        make_tool(self.bin / "pdfinfo", 'print("Pages: 5")\n')
        make_tool(self.bin / "pdftoppm", r'''
            import json, struct, zlib
            from pathlib import Path
            here = Path(__file__).parent
            calls = here / "calls.json"
            history = json.loads(calls.read_text()) if calls.exists() else []
            first = int(sys.argv[sys.argv.index("-f") + 1])
            last = int(sys.argv[sys.argv.index("-l") + 1])
            history.append([first, last])
            calls.write_text(json.dumps(history))
            options = json.loads((here / "options.json").read_text()) if (here / "options.json").exists() else {}
            if options.get("fail") == first:
                print("deliberate rendering failure")
                sys.exit(1)
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")
            for page in range(first, last + 1):
                if options.get("omit") != page:
                    Path(sys.argv[-1] + f"-{page:02d}.png").write_bytes(png)
            if options.get("mutate_pdf"):
                Path(sys.argv[-2]).write_bytes(b"%PDF-changed-during-render")
        ''')

    def invoke(self, *args, ok=True):
        result = run_script("render_pdf.py", self.pdf, "--output", self.root / "tmp" / "render-zh",
                            "--pdftoppm", self.bin / "pdftoppm", "--pdfinfo", self.bin / "pdfinfo", "--json", *args)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def configure(self, **options):
        (self.bin / "options.json").write_text(json.dumps(options))

    def test_full_render_batches_pages_and_verified_reuse_runs_no_renderer(self):
        first = self.invoke()
        self.assertEqual((first["rendered"], first["reused"]), (5, 0))
        self.assertEqual(len(first["files"]), 3)
        second = self.invoke("--details")
        self.assertEqual((second["rendered"], second["reused"]), (0, 5))
        self.assertEqual(len(second["files"]), 5)
        self.assertEqual(json.loads((self.bin / "calls.json").read_text()), [[1, 5]])

    def test_only_missing_and_changed_pages_render_again(self):
        first = self.invoke("--details")
        files = [Path(path) for path in first["files"]]
        unchanged = files[0].stat().st_mtime_ns
        files[1].unlink()
        files[3].write_bytes(b"corrupt")
        fixed = self.invoke()
        self.assertEqual((fixed["rendered"], fixed["reused"]), (2, 3))
        self.assertEqual(json.loads((self.bin / "calls.json").read_text()), [[1, 5], [2, 2], [4, 4]])
        self.assertEqual(files[0].stat().st_mtime_ns, unchanged)

    def test_dpi_pdf_and_renderer_changes_use_separate_directories(self):
        first = self.invoke()
        high = self.invoke("--dpi", "180", "--pages", "2,4-5")
        self.assertNotEqual(first["render_dir"], high["render_dir"])
        self.assertEqual(high["rendered"], 3)
        self.pdf.write_bytes(b"%PDF-revised")
        revised = self.invoke()
        self.assertNotEqual(first["render_dir"], revised["render_dir"])
        engine = self.bin / "pdftoppm"
        engine.write_text(engine.read_text() + "\n# version changed\n")
        updated = self.invoke()
        self.assertNotEqual(revised["render_dir"], updated["render_dir"])

    def test_force_only_replaces_requested_pages(self):
        self.invoke()
        result = self.invoke("--force", "--pages", "3")
        self.assertEqual((result["requested_count"], result["rendered"]), (1, 1))
        self.assertEqual(self.invoke()["reused"], 5)

    def test_failed_later_range_keeps_previous_range_checkpoint(self):
        self.configure(fail=4)
        result = self.invoke("--pages", "1,4", ok=False)
        self.assertIn("deliberate rendering failure", result["error"])
        self.configure()
        fixed = self.invoke("--pages", "1,4")
        self.assertEqual((fixed["rendered"], fixed["reused"]), (1, 1))

    def test_missing_output_or_changed_pdf_is_never_marked_complete(self):
        for options in ({"omit": 3}, {"mutate_pdf": True}):
            with self.subTest(options=options):
                self.configure(**options)
                self.invoke(ok=False)
                self.assertFalse(list((self.root / "tmp").rglob("manifest.json")))

    def test_invalid_page_ranges_and_corrupt_metadata(self):
        for pages in ("0", "6", "4-2", "1,a", "-1"):
            with self.subTest(pages=pages):
                self.invoke("--pages=" + pages, ok=False)
        first = self.invoke()
        (Path(first["render_dir"]) / "manifest.json").write_text("[]")
        self.assertEqual(self.invoke()["rendered"], 5)


if __name__ == "__main__":
    unittest.main()
