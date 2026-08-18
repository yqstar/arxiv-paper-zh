#!/usr/bin/env python3
"""Build with XeLaTeX, select BibTeX/Biber, and audit the final log."""
from __future__ import annotations
import argparse, re, shutil, subprocess
from pathlib import Path
ERROR = re.compile(r"LaTeX Error|Undefined control sequence|Emergency stop|Fatal error|undefined references|Citation .* undefined|Reference .* undefined|Missing character")
def tool(name: str, bindir: Path | None) -> str:
    candidate = bindir / name if bindir else None; result = str(candidate) if candidate and candidate.exists() else shutil.which(name)
    if not result: raise SystemExit(f"missing executable: {name}")
    return result
def run(command: list[str], cwd: Path) -> None: print("+ " + " ".join(command)); subprocess.run(command, cwd=cwd, check=True)
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("entry", type=Path); parser.add_argument("--tex-bin", type=Path); args = parser.parse_args()
    entry = args.entry.resolve(); args.tex_bin = args.tex_bin.resolve() if args.tex_bin else None; cwd, stem = entry.parent, entry.stem; source = entry.read_text(encoding="utf-8", errors="replace")
    command = [tool("xelatex", args.tex_bin), "-interaction=nonstopmode", "-halt-on-error", entry.name]; run(command, cwd)
    if re.search(r"\\(?:usepackage(?:\[[^]]*\])?\{biblatex\}|addbibresource)", source): run([tool("biber", args.tex_bin), stem], cwd)
    elif re.search(r"\\bibliography\s*\{", source): run([tool("bibtex", args.tex_bin), stem], cwd)
    run(command, cwd); run(command, cwd)
    hits = [line for line in (cwd / f"{stem}.log").read_text(encoding="utf-8", errors="replace").splitlines() if ERROR.search(line)]
    if hits: print("\n".join(hits)); return 2
    pdf = cwd / f"{stem}.pdf"
    if not pdf.is_file() or not pdf.stat().st_size: print(f"missing output: {pdf}"); return 3
    print(f"build_ok={pdf}"); return 0
if __name__ == "__main__": raise SystemExit(main())
