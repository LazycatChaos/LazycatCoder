"""Main entry point for running evals.

Usage:
    python -m eval.run                          # Run all cases with default config
    python -m eval.run --case case_001          # Run a specific case
    python -m eval.run --model gpt-4o           # Override model
    python -m eval.run --debug                  # Enable debug output
"""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.sandbox import Sandbox
from eval.runner import EvalRunner
from eval.evaluator import Evaluator
from eval.report import print_report


# ---------------------------------------------------------------------------
# Case definitions — add new cases here
# ---------------------------------------------------------------------------

CASES = [
    {
        "case_id": "case_001_divide_by_zero",
        "type": "bug_fix",
        "prompt": (
            "Fix the bug in `math_utils.py`. The `safe_divide` function should return 0 "
            "when dividing by zero instead of raising a ZeroDivisionError. "
            "Do not modify any other functions. Read the file first, then make the fix."
        ),
    },
]


def find_case_dir(case_id: str) -> Path:
    """Locate a case directory under eval/datasets/."""
    datasets_dir = Path(__file__).resolve().parent / "datasets"
    case_dir = datasets_dir / case_id
    if not case_dir.exists():
        raise FileNotFoundError(f"Case directory not found: {case_dir}")
    return case_dir


def run_single_case(case: dict, runner: EvalRunner, evaluator: Evaluator, debug: bool = False) -> dict:
    """Run one eval case: sandbox → agent → evaluate → cleanup."""
    case_id = case["case_id"]
    prompt = case["prompt"]

    if debug:
        print(f"\n{'='*60}")
        print(f"Running: {case_id}")
        print(f"{'='*60}")

    sandbox = Sandbox(find_case_dir(case_id))

    try:
        # 1. Setup sandbox (copy workspace to temp dir)
        workdir = sandbox.setup()
        if debug:
            print(f"  Sandbox: {workdir}")

        # 2. Run the agent
        result = runner.run(str(workdir), prompt)

        if debug:
            print(f"  Agent rounds: {result['rounds']}, tokens: {result['total_tokens']}")
            if result["error"]:
                print(f"  Agent error: {result['error']}")

        # 3. Inject hidden tests
        sandbox.inject_tests()

        # 4. Evaluate
        eval_result = evaluator.evaluate(str(workdir))

        if debug:
            print(f"  Tests passed: {eval_result['passed']}")
            if eval_result["stderr"]:
                print(f"  Test output: {eval_result['stderr'][:200]}")

        return {
            "case_id": case_id,
            "passed": eval_result["passed"],
            "rounds": result["rounds"],
            "total_tokens": result["total_tokens"],
            "error": result["error"],
            "test_output": eval_result["stderr"] or eval_result["stdout"],
            "timed_out": eval_result.get("timed_out", False),
        }

    except Exception as e:
        if debug:
            print(f"  Exception: {e}")
        return {
            "case_id": case_id,
            "passed": False,
            "rounds": 0,
            "total_tokens": 0,
            "error": str(e),
            "test_output": None,
            "timed_out": False,
        }

    finally:
        # 5. Cleanup sandbox
        sandbox.teardown()


def main():
    parser = argparse.ArgumentParser(description="LazyCatCoder Eval Runner")
    parser.add_argument("--case", help="Run a specific case (e.g. case_001_divide_by_zero)")
    parser.add_argument("--model", help="Override model name")
    parser.add_argument("--api-key", help="Override API key")
    parser.add_argument("--base-url", help="Override API base URL")
    parser.add_argument("--max-rounds", type=int, default=10, help="Max agent rounds per case")
    parser.add_argument("--system-prompt", help="Path to a custom system prompt file (.md/.txt)")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    # Load custom system prompt if provided
    custom_system_prompt = None
    if args.system_prompt:
        prompt_path = Path(args.system_prompt)
        if not prompt_path.exists():
            print(f"Error: system prompt file not found: {prompt_path}")
            sys.exit(1)
        custom_system_prompt = prompt_path.read_text(encoding="utf-8")
        print(f"[Eval] Using custom system prompt: {prompt_path}")

    # Filter cases
    cases = CASES
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"Error: case '{args.case}' not found")
            sys.exit(1)

    # Initialize runner and evaluator
    runner = EvalRunner(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_rounds=args.max_rounds,
        debug=args.debug,
        system_prompt=custom_system_prompt,
    )
    evaluator = Evaluator()

    print(f"\n[Eval] Running {len(cases)} eval case(s) with model: {runner.model}")
    print(f"   Max rounds: {args.max_rounds}\n")

    results = []
    for case in cases:
        result = run_single_case(case, runner, evaluator, debug=args.debug)
        results.append(result)

    # Print report
    print()
    print_report(results)


if __name__ == "__main__":
    main()
