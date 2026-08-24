#!/usr/bin/env python3
"""Check Codex Python requirements and importability."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None

IMPORT_NAMES = {
    "python-docx": "docx",
    "PyPDF2": "PyPDF2",
    "python-jose": "jose",
    "slack-sdk": "slack_sdk",
    "rank-bm25": "rank_bm25",
    "psycopg2-binary": "psycopg2",
    "scikit-learn": "sklearn",
    "argon2-cffi": "argon2",
    "python-dotenv": "dotenv",
}


def requirements(path: Path):
    if Requirement is None:
        raise RuntimeError("packaging is not installed")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        yield Requirement(line)


def pip_install(root: Path, quiet: bool) -> bool:
    python = sys.executable
    if not quiet:
        print("Installing missing Codex Python dependencies...")
    command = [python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
    if subprocess.run(command, check=False).returncode != 0:
        return False
    return subprocess.run(
        [python, "-m", "pip", "install", "-r", str(root / "requirements.txt")],
        check=False,
    ).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only report problems; do not install missing packages",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    req_path = root / "requirements.txt"

    global Requirement
    if Requirement is None:
        if args.check_only or not pip_install(root, args.quiet):
            print("ERROR: packaging is required to run the dependency checker", file=sys.stderr)
            return 2
        from packaging.requirements import Requirement as ParsedRequirement
        Requirement = ParsedRequirement

    if sys.version_info < (3, 10):
        print("FAIL Python: Python 3.10+ is required")
        return 1
    if not args.quiet:
        print(f"Python: {sys.executable} ({sys.version.split()[0]})")

    def check() -> int:
        failures = 0
        for requirement in requirements(req_path):
            name = requirement.name
            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                print(f"MISSING {name} ({requirement})")
                failures += 1
                continue
            if requirement.specifier and installed not in requirement.specifier:
                print(f"WRONG VERSION {name}: installed {installed}, need {requirement.specifier}")
                failures += 1
                continue
            module = IMPORT_NAMES.get(name, re.sub(r"[-.]", "_", name))
            try:
                importlib.import_module(module)
            except Exception as exc:
                print(f"IMPORT FAILED {name} ({module}): {exc}")
                failures += 1
                continue
            if not args.quiet:
                print(f"OK {name}=={installed}")
        return failures

    failures = check()
    if failures and not args.check_only:
        if pip_install(root, args.quiet):
            failures = check()

    if failures:
        print(f"Dependency check failed: {failures} issue(s).", file=sys.stderr)
        return 1
    print("All Codex Python dependencies are installed and importable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
