#!/usr/bin/env python3
"""Check or batch-install paper-translation and document-specific TeX packages."""
from __future__ import annotations
import argparse, os, re, shutil, subprocess
from pathlib import Path

USEPACKAGE = re.compile(r"\\usepackage(?:\[[^]]*\])?\{([^}]+)\}")
ALIASES = {"balance": "preprint", "manyfoot": "ncctools", "tikzfill.image": "tikzfill", "xspace": "tools", "graphicx": "graphics", "pifont": "psnfss", "xfrac": "l3packages"}
TRANSITIVE = {"algorithm2e": ["ifoddpage", "relsize"], "tcolorbox": ["tikzfill", "pdfcol"]}
PRESET = Path(__file__).resolve().parent.parent / "references" / "paper-translation-packages.txt"

def executable(value: str | None, fallback: str) -> str:
    result = value or shutil.which(fallback)
    if not result: raise SystemExit(f"missing executable: {fallback}")
    path = Path(result).expanduser()
    return os.path.abspath(path) if path.exists() else result

def read_preset() -> dict[str, str]:
    result = {}
    for raw in PRESET.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            package, probe = line.split("|", 1); result[package.strip()] = probe.strip()
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--kpsewhich")
    parser.add_argument("--tlmgr")
    parser.add_argument("--preset", action="store_true", help="include the reusable paper-translation preset")
    parser.add_argument("--install", action="store_true", help="install all missing packages in one tlmgr call")
    args = parser.parse_args()
    if not args.root and not args.preset: parser.error("provide root and/or --preset")
    kpsewhich = executable(args.kpsewhich, "kpsewhich")
    candidates: dict[str, str] = read_preset() if args.preset else {}
    if args.root:
        for path in args.root.resolve().rglob("*.tex"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for group in USEPACKAGE.findall(text):
                for name in (x.strip() for x in group.split(",") if x.strip()):
                    package = ALIASES.get(name, name); candidates.setdefault(package, f"{name}.sty")
                    for dependency in TRANSITIVE.get(name, []): candidates.setdefault(dependency, f"{dependency}.sty")
    missing = []; tex_bin = Path(kpsewhich).resolve().parent
    for package, probe in sorted(candidates.items()):
        found = (tex_bin / probe.removeprefix("bin:")).exists() if probe.startswith("bin:") else bool(subprocess.run([kpsewhich, probe], capture_output=True, text=True).stdout.strip())
        if not found: missing.append(package)
    print(f"preset_file={PRESET}" if args.preset else "preset_file=")
    print("checked_packages=" + " ".join(sorted(candidates)))
    print("missing_packages=" + " ".join(missing))
    if args.install and missing:
        return subprocess.run([executable(args.tlmgr, "tlmgr"), "install", *missing]).returncode
    return 0

if __name__ == "__main__": raise SystemExit(main())
