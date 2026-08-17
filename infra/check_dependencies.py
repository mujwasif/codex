#!/usr/bin/env python3
"""Check Codex Python requirements and importability."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import re
import sys
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:
    print("ERROR: packaging is required to run the dependency checker", file=sys.stderr)
    raise SystemExit(2)

IMPORT_NAMES = {
    "python-docx": "docx",
    "PyPDF2": "PyPDF2",
    "python-jose": "jose",
    "slack-sdk": "slack_sdk",
    "rank-bm25": "rank_bm25",
    "psycopg2-binary": "psycopg2",
    "scikit-learn": "sklearn",
}


def requirements(path: Path):
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        yield Requirement(line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    req_path = root / "requirements.txt"
    failures = 0

    if sys.version_info < (3, 10):
        print("FAIL Python: Python 3.10+ is required")
        return 1
    if not args.quiet:
        print(f"Python: {sys.executable} ({sys.version.split()[0]})")

    for requirement in requirements(req_path):
        name = requirement.name
        if name == "torch" and os.getenv("CODEX_CUDA", "false").lower() == "true":
            requirement = next(
                req for req in requirements(root / "requirements-cuda.txt") if req.name == "torch"
            )
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

    try:
        import torch
        print(f"CUDA: {'available' if torch.cuda.is_available() else 'not available'} ({torch.__version__})")
    except Exception as exc:
        print(f"CUDA CHECK FAILED: {exc}")
        failures += 1

    if failures:
        print(f"Dependency check failed: {failures} issue(s).", file=sys.stderr)
        return 1
    print("All Codex Python dependencies are installed and importable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
