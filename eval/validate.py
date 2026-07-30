"""Pre-check: validate that all cases are well-formed before running Agent evals.

This is a pure-script validation (no LLM involved). Run it manually when
you add or modify cases.

Usage:
    python -m eval.validate              # Validate all cases
    python -m eval.validate --case case_001  # Validate a specific case
"""

import sys
import subprocess
import tempfile
import shutil
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.run import load_cases, DATASETS_DIR


def validate_case(case: dict) -> dict:
    """Validate a single case.

    Checks:
    1. golden/ exists and passes all hidden_tests
    2. workspace/ exists and FAILS hidden_tests (has differentiation)

    Returns:
        {"ok": bool, "reason": str, "details": dict}
    """
    case_id = case["case_id"]
    case_dir = case["case_dir"]
    golden_dir = case_dir / "golden"
    workspace_dir = case_dir / "workspace"
    hidden_tests_dir = case_dir / "hidden_tests"

    # Check required directories exist
    for d, name in [(workspace_dir, "workspace"), (hidden_tests_dir, "hidden_tests"), (golden_dir, "golden")]:
        if not d.exists():
            return {"ok": False, "reason": f"Missing directory: {name}/", "details": {}}

    # Check golden has at least one file
    golden_files = list(golden_dir.rglob("*"))
    golden_files = [f for f in golden_files if f.is_file()]
    if not golden_files:
        return {"ok": False, "reason": "golden/ is empty", "details": {}}

    details = {}

    # --- Test 1: golden should PASS hidden tests ---
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Copy golden files to tmpdir root
        for f in golden_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(golden_dir)
                dst = tmpdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        # Copy hidden tests
        for f in hidden_tests_dir.rglob("*"):
            if f.is_file():
                dst = tmpdir / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)

        test_files = list(tmpdir.glob("test_*.py"))
        if not test_files:
            return {"ok": False, "reason": "No test_*.py files in hidden_tests/", "details": {}}

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
            cwd=str(tmpdir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        golden_passed = result.returncode == 0
        details["golden_passed"] = golden_passed
        if not golden_passed:
            return {
                "ok": False,
                "reason": f"golden/ FAILED hidden tests",
                "details": details,
            }

    # --- Test 2: workspace should FAIL hidden tests (differentiation) ---
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Copy workspace files to tmpdir root
        for f in workspace_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(workspace_dir)
                dst = tmpdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        # Copy hidden tests
        for f in hidden_tests_dir.rglob("*"):
            if f.is_file():
                dst = tmpdir / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
            cwd=str(tmpdir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        workspace_failed = result.returncode != 0
        details["workspace_failed"] = workspace_failed
        if not workspace_failed:
            return {
                "ok": False,
                "reason": "workspace/ PASSES hidden tests (no differentiation — the bug doesn't exist!)",
                "details": details,
            }

    return {"ok": True, "reason": "OK", "details": details}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate eval cases (no LLM)")
    parser.add_argument("--case", help="Validate a specific case")
    parser.add_argument("--datasets-dir", type=Path, default=DATASETS_DIR)
    args = parser.parse_args()

    cases = load_cases(args.datasets_dir)
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"Error: case '{args.case}' not found")
            sys.exit(1)

    print(f"\nValidating {len(cases)} case(s)...\n")

    all_ok = True
    for case in cases:
        result = validate_case(case)
        icon = "[PASS]" if result["ok"] else "[FAIL]"
        print(f"  {icon} {case['case_id']}: {result['reason']}")
        if not result["ok"]:
            all_ok = False
            details = result.get("details", {})
            if details.get("golden_passed") is False:
                print(f"     -> golden/ code failed hidden tests. Fix the golden solution first.")
            if details.get("workspace_failed") is False:
                print(f"     -> workspace/ already passes hidden tests. The case has no differentiation.")

    print()
    if all_ok:
        print("All cases are valid. Ready to run Agent evals.")
    else:
        print("Some cases are invalid. Fix them before running Agent evals.")
        sys.exit(1)


if __name__ == "__main__":
    main()
