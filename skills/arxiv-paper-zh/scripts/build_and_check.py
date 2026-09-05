#!/usr/bin/env python3
"""Build until references converge, reusing only verified successful artifacts."""
from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from artifact_cache import file_hash, hashes_match, load_cache, save_cache, value_hash


FATAL = re.compile(r"LaTeX Error|Undefined control sequence|Emergency stop|Fatal error|Missing character", re.I)
UNRESOLVED = re.compile(r"undefined (?:references|citations)|(?:Citation|Reference) .* undefined", re.I)
RERUN = re.compile(r"Rerun to get|Please (?:\(re\))?run (?:LaTeX|Biber|BibTeX)|Please rerun|Label\(s\) may have changed", re.I)
CONVERGENCE_SUFFIXES = {".aux", ".toc", ".lof", ".lot", ".out", ".bbl", ".bcf", ".nav", ".snm"}
GENERATED_SUFFIXES = CONVERGENCE_SUFFIXES | {".log", ".blg", ".fls", ".xdv", ".fdb_latexmk"}
TEX_ENV = ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS", "TEXMF", "TEXMFHOME", "TEXMFLOCAL", "TEXMFVAR", "TEXMFCNF", "TEXMFCONFIG", "OSFONTDIR", "FONTCONFIG_PATH", "FONTCONFIG_FILE", "SOURCE_DATE_EPOCH", "TZ", "LANG", "LC_ALL")


def tool(name: str, bindir: Path | None) -> str:
    candidate = bindir / name if bindir else None
    result = str(candidate) if candidate and candidate.exists() else shutil.which(name)
    if not result:
        raise ValueError(f"missing executable: {name}")
    return result


def diagnostic_path(entry: Path) -> Path:
    for directory in entry.parents:
        if directory.name in ("paper-en", "paper-zh") and directory.parent.name == "latex":
            return directory.parent.parent / "tmp" / f"{directory.name}-build.log"
    return entry.with_suffix(".build.log")


def source_root(entry: Path) -> Path:
    for directory in entry.parents:
        if directory.name in ("paper-en", "paper-zh") and directory.parent.name == "latex":
            return directory
    return entry.parent


def visible_files(root: Path):
    return (path for path in root.rglob("*") if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts))


def generated(path: Path) -> bool:
    return path.suffix in GENERATED_SUFFIXES or path.name.endswith((".run.xml", ".synctex.gz"))


def local_inputs(root: Path, entry: Path, outputs: set[Path]) -> dict:
    return {str(path.resolve()): file_hash(path) for path in visible_files(root)
            if not generated(path) and path.resolve() != entry.with_suffix(".pdf") and path.resolve() not in outputs}


def convergence_files(root: Path) -> dict:
    return {str(path.resolve()): file_hash(path) for path in visible_files(root)
            if path.suffix in CONVERGENCE_SUFFIXES or path.name.endswith(".run.xml")}


def recorder(entry: Path) -> tuple[set[Path], set[Path]]:
    inputs, outputs = set(), set()
    path = entry.with_suffix(".fls")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            kind, separator, name = line.partition(" ")
            if separator and kind in ("INPUT", "OUTPUT"):
                target = (entry.parent / name).resolve()
                (inputs if kind == "INPUT" else outputs).add(target)
    return inputs, outputs


def aux_tree(entry: Path) -> list[Path]:
    pending = [entry.with_suffix(".aux")]
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"\\@input\{([^}]+)\}", text):
            child = (entry.parent / name).resolve()
            pending.append(child if child.is_file() else path.parent / name)
    return sorted(seen)


def bibliography(entry: Path, outputs: set[Path], source_hint: str) -> tuple[str | None, str, list[str]]:
    bcf = entry.with_suffix(".bcf")
    if bcf.is_file() and (bcf in outputs or re.search(r"\\(?:addbibresource|usepackage(?:\[[^]]*\])?\{biblatex\})", source_hint)):
        text = bcf.read_text(encoding="utf-8")
        files = [element.text.strip() for element in ET.fromstring(text).iter()
                 if element.tag.split("}")[-1] == "datasource" and element.text]
        return "biber", text, files
    controls = []
    files = []
    for path in aux_tree(entry):
        text = path.read_text(encoding="utf-8", errors="replace")
        controls.extend(re.findall(r"\\(?:citation|bibdata|bibstyle)\{[^}]*\}", text))
        for kind, group in re.findall(r"\\(bibdata|bibstyle)\{([^}]+)\}", text):
            suffix = ".bib" if kind == "bibdata" else ".bst"
            files.extend(name.strip() if name.strip().endswith(suffix) else name.strip() + suffix for name in group.split(","))
    if any(control.startswith(r"\bibdata") for control in controls):
        return "bibtex", "\n".join(controls), files
    return None, "", []


def resolve_bib_inputs(names: list[str], entry: Path, bindir: Path | None) -> tuple[dict, list[str]]:
    dependencies, missing = {}, []
    kpse = bindir / "kpsewhich" if bindir else None
    executable = str(kpse) if kpse and kpse.is_file() else shutil.which("kpsewhich")
    for name in names:
        path = (entry.parent / name).resolve()
        if not path.is_file() and executable:
            result = subprocess.run([executable, name], cwd=entry.parent, capture_output=True, text=True)
            found = result.stdout.strip().splitlines()
            if result.returncode == 0 and found:
                path = (entry.parent / found[0]).resolve()
        if path.is_file():
            dependencies[str(path)] = file_hash(path)
        else:
            missing.append(name)
    return dependencies, missing


def run(command: list[str], cwd: Path, log_path: Path, verbose: bool, json_output: bool = False) -> bool:
    with log_path.open("a", encoding="utf-8") as log:
        log.write("+ " + " ".join(command) + "\n")
        log.flush()
        offset = log.tell()
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    stream = sys.stderr if json_output else sys.stdout
    if verbose:
        print("+ " + " ".join(command), file=stream)
        with log_path.open(encoding="utf-8", errors="replace") as log:
            log.seek(offset)
            print(log.read(), end="", file=stream)
    if result.returncode:
        print(f"build_failed={Path(command[0]).name} exit_code={result.returncode} build_log={log_path}", file=stream)
        if not verbose:
            with log_path.open(encoding="utf-8", errors="replace") as log:
                for line in deque(log, maxlen=12):
                    print(line.rstrip()[:240], file=stream)
        return False
    return True


def emit(payload: dict, args) -> None:
    if args.json:
        if not args.verbose and "errors" in payload:
            payload = {**payload, "errors": [line[:240] for line in payload["errors"][:5]]}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if payload.get("ok"):
            print(f"build_ok={payload['pdf']} cached={str(payload['cached']).lower()} runs={payload['runs']} bibliography_runs={payload['bibliography_runs']} elapsed_seconds={payload['elapsed_seconds']} build_log={payload['build_log']}")
            if payload.get("cache_disabled"):
                print("cache_disabled=" + "; ".join(payload["cache_disabled"][:3]))
        else:
            print(f"build_errors={payload.get('error_count', 1)} runs={payload.get('runs', 0)} build_log={payload['build_log']}")
            for line in payload.get("errors", []) if args.verbose else payload.get("errors", [])[:5]:
                print(line if args.verbose else line[:240])


def build(args) -> int:
    started = time.monotonic()
    entry = args.entry.resolve()
    bindir = args.tex_bin.resolve() if args.tex_bin else None
    root = args.source_root.resolve() if args.source_root else source_root(entry)
    if not entry.is_file() or root not in entry.parents:
        raise ValueError("entry must be an existing file below --source-root")
    log_path = args.log_file.resolve() if args.log_file else diagnostic_path(entry)
    if log_path in (entry, entry.with_suffix(".log"), entry.with_suffix(".pdf")):
        raise ValueError("--log-file must not overwrite the entry, TeX log or PDF")
    engine = Path(tool(args.engine, bindir))
    command = [str(engine), "-interaction=nonstopmode", "-halt-on-error", "-recorder", entry.name]
    environment = {name: os.environ.get(name) for name in TEX_ENV}
    config = {"entry": str(entry), "root": str(root), "command": command, "engine": file_hash(engine),
              "environment": environment, "script": [file_hash(Path(__file__)), file_hash(Path(__file__).with_name("artifact_cache.py"))]}
    cache_path = log_path.parent / ".build-cache" / f"{value_hash(str(entry))[:16]}.json"
    cached = load_cache(cache_path)
    if not isinstance(cached.get("generated_outputs", []), list) or not all(isinstance(path, str) for path in cached.get("generated_outputs", [])):
        cached = {}
    if not isinstance(cached.get("bibliography", {}), dict):
        cached = {}
    pdf = entry.with_suffix(".pdf")
    baseline_outputs = {Path(path) for path in cached.get("generated_outputs", [])}
    initial = local_inputs(root, entry, set())
    initial_for_cache = {path: value for path, value in initial.items() if Path(path) not in baseline_outputs}
    if not args.force and cached.get("version") == 1 and cached.get("config") == config and cached.get("local_inputs") == initial_for_cache:
        if hashes_match(cached.get("dependencies", {})) and hashes_match(cached.get("artifacts", {})):
            emit({"ok": True, "pdf": str(pdf), "cached": True, "runs": 0, "bibliography_runs": 0,
                  "elapsed_seconds": round(time.monotonic() - started, 3), "build_log": str(log_path)}, args)
            return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    # Retain the executable name (xelatex/pdftex can be symlinks to a shared engine).
    command[0] = tool(args.engine, bindir)
    previous_bib = cached.get("bibliography", {}) if not args.force and cached.get("config") == config else {}
    bib_state, bib_runs, dependencies, outputs, cache_disabled = {}, 0, {}, set(), []
    previous_state = convergence_files(root)
    errors = []
    for run_number in range(1, args.max_runs + 1):
        if not run(command, entry.parent, log_path, args.verbose, args.json):
            if args.json:
                emit({"ok": False, "runs": run_number, "errors": ["compiler failed"], "build_log": str(log_path)}, args)
            return 2
        tex_log = entry.with_suffix(".log")
        if not tex_log.is_file():
            errors = [f"missing_log={tex_log}"]
            break
        lines = tex_log.read_text(encoding="utf-8", errors="replace").splitlines()
        fatal = [line for line in lines if FATAL.search(line)]
        if fatal:
            errors = fatal
            break
        inputs, outputs = recorder(entry)
        dependencies = {str(path): file_hash(path) for path in inputs - outputs if path.is_file()}
        source_hint = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in visible_files(root) if path.suffix == ".tex")
        kind, controls, bib_names = bibliography(entry, outputs, source_hint)
        ran_bib = False
        if kind:
            executable = Path(tool(kind, bindir))
            bib_dependencies, unresolved_files = resolve_bib_inputs(bib_names, entry, bindir)
            dependencies.update(bib_dependencies)
            dependencies[str(executable.resolve())] = file_hash(executable)
            signature = value_hash([kind, controls, bib_dependencies, unresolved_files, file_hash(executable), environment])
            bbl = entry.with_suffix(".bbl")
            prior = bib_state or previous_bib
            if signature != prior.get("signature") or not file_hash(bbl) or file_hash(bbl) != prior.get("bbl_hash"):
                if not run([str(executable), entry.stem], entry.parent, log_path, args.verbose, args.json):
                    if args.json:
                        emit({"ok": False, "runs": run_number, "errors": [f"{kind} failed"], "build_log": str(log_path)}, args)
                    return 2
                bib_runs += 1
                ran_bib = True
            if not bbl.is_file() or not bbl.stat().st_size:
                errors = [f"missing bibliography output: {bbl}"]
                break
            bib_state = {"signature": signature, "bbl_hash": file_hash(bbl)}
            cache_disabled = ["unresolved bibliography inputs: " + ", ".join(unresolved_files)] if unresolved_files else []
        current_state = convergence_files(root)
        unresolved = [line for line in lines if UNRESOLVED.search(line) or RERUN.search(line)]
        if not ran_bib and not unresolved and current_state == previous_state:
            if not pdf.is_file() or not pdf.stat().st_size:
                errors = [f"missing output: {pdf}"]
                break
            current_inputs = local_inputs(root, entry, outputs)
            before = {path: value for path, value in initial.items() if Path(path) not in outputs}
            if current_inputs != before:
                errors = ["source inputs changed during build; run again with stable inputs"]
                break
            if not inputs or not entry.with_suffix(".fls").is_file():
                cache_disabled.append("recorder did not capture dependencies")
            if any(not path.is_file() for path in inputs - outputs):
                cache_disabled.append("recorded input no longer exists")
            artifacts = {**current_state, str(pdf): file_hash(pdf), str(tex_log): file_hash(tex_log)}
            if not cache_disabled:
                save_cache(cache_path, {"version": 1, "config": config, "local_inputs": current_inputs,
                    "dependencies": dependencies, "artifacts": artifacts, "bibliography": bib_state,
                    "generated_outputs": sorted(str(path) for path in outputs)})
            emit({"ok": True, "pdf": str(pdf), "cached": False, "runs": run_number,
                  "bibliography_runs": bib_runs, "cache_disabled": cache_disabled,
                  "elapsed_seconds": round(time.monotonic() - started, 3), "build_log": str(log_path)}, args)
            return 0
        previous_state = current_state
        errors = unresolved or [f"auxiliary files have not converged after {run_number} runs"]
    emit({"ok": False, "runs": run_number, "error_count": len(errors), "errors": errors,
          "build_log": str(log_path)}, args)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("entry", type=Path)
    parser.add_argument("--tex-bin", type=Path)
    parser.add_argument("--source-root", type=Path, help="complete source tree; inferred for the standard paper layout")
    parser.add_argument("--engine", choices=("xelatex", "pdflatex", "lualatex"), default="xelatex")
    parser.add_argument("--max-runs", type=int, default=6)
    parser.add_argument("--force", action="store_true", help="rebuild even when the successful build cache matches")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--verbose", action="store_true", help="also print full command output")
    args = parser.parse_args()
    if args.max_runs < 1:
        parser.error("--max-runs must be positive")
    try:
        return build(args)
    except (OSError, ValueError, ET.ParseError) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}, ensure_ascii=False) if args.json else f"build_error={error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
