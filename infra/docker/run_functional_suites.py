#!/usr/bin/env python3
"""
Run every bare-function suite under tests/ that unittest discovery cannot collect.

`python -m unittest discover -s tests -v` only collects unittest.TestCase
subclasses. Nine suites in this repo are plain-function suites driven by a
module-level main() helper (returning bool or 0/1), so discovery never runs
them. This runner executes each one in-process, normalizes its return value,
and exits nonzero if any suite fails or raises.

test_reasoning.py is special: it is an unasserted live-API probe (it POSTs to
http://127.0.0.1:8000 and only prints outcomes; it defines no assertions and
always exits 0 by construction). It is executed as a subprocess for output
fidelity and recorded as informational-by-design — real end-to-end coverage of
the live stack belongs to infra/smoke.sh (plan task 5 / ISC-5, ISC-6).
"""

import importlib
import subprocess
import sys
import time
import traceback

REPO_ROOT = "/app"
TESTS_DIR = "/app/tests"
for _p in (REPO_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Bare-function suites with a main() entrypoint (bool or int return).
FUNCTIONAL_SUITES = [
    "test_answer_formatting",
    "test_chunking",
    "test_clause_detection_cot",
    "test_graph_sanitizer",
    "test_history",
    "test_rbac",
    # retrieval_quality last among heavy ones: imports services.api.search which
    # eagerly loads bge-large + bge-reranker (cached in-process afterwards).
    "test_retrieval_quality",
]

# Unasserted live-API probes: no assertions by construction (exit code always 0).
LIVE_PROBE_SUITES = ["test_reasoning"]


def _run_suite(name: str) -> bool:
    t0 = time.time()
    try:
        mod = importlib.import_module(name)
    except Exception:
        traceback.print_exc()
        print(f"SUITE {name}: FAIL (import error)", flush=True)
        return False

    fn = getattr(mod, "main", None)
    if fn is None:
        print(f"SUITE {name}: FAIL (no main() entrypoint)", flush=True)
        return False

    try:
        rc = fn()
    except SystemExit as exc:  # some mains sys.exit() directly
        rc = exc.code
    except Exception:
        traceback.print_exc()
        print(f"SUITE {name}: FAIL (exception)", flush=True)
        return False

    ok = rc if isinstance(rc, bool) else (rc == 0)
    status = "PASS" if ok else f"FAIL (rc={rc!r})"
    print(f"SUITE {name}: {status} ({time.time() - t0:.1f}s)", flush=True)
    return ok


def main() -> int:
    failures = []
    print("=" * 70)
    print("FUNCTIONAL SUITES (bare-function main() runners)")
    print("=" * 70, flush=True)

    for name in FUNCTIONAL_SUITES:
        if not _run_suite(name):
            failures.append(name)

    for name in LIVE_PROBE_SUITES:
        rc = subprocess.call([sys.executable, f"tests/{name}.py"])
        print(
            f"PROBE {name}: ran rc={rc} "
            "(unasserted live-API probe; e2e owned by infra/smoke.sh)",
            flush=True,
        )
        if rc != 0:
            failures.append(name)

    print("=" * 70)
    if failures:
        print(f"FUNCTIONAL RESULT: FAIL — failing suites: {', '.join(failures)}")
        return 1
    print("FUNCTIONAL RESULT: PASS — all suites green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
