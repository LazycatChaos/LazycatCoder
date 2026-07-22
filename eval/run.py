"""Main entry point for running evals.

Usage:
    python -m eval.run                          # Run all cases with default config
    python -m eval.run --case case_001          # Run a specific case
    python -m eval.run --model gpt-4o           # Override model
    python -m eval.run --debug                  # Enable debug output
"""

import argparse
import json
import sys
import yaml
from datetime import datetime
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
# Case loading — scan datasets/ for meta.yaml files
# ---------------------------------------------------------------------------

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def load_cases(datasets_dir: Path = DATASETS_DIR) -> list[dict]:
    """Recursively scan datasets/ for case directories with meta.yaml.

    Skips directories starting with '_' (e.g. _template).

    Returns list of dicts with keys:
        case_id, case_dir, meta (dict), prompt (str)
    """
    cases = []
    for meta_path in sorted(datasets_dir.glob("**/meta.yaml")):
        # Skip _template and other underscore-prefixed dirs
        if meta_path.parent.name.startswith("_"):
            continue

        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        prompt_path = meta_path.parent / "prompt.md"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""

        cases.append({
            "case_id": meta["id"],
            "case_dir": meta_path.parent,
            "meta": meta,
            "prompt": prompt,
        })

    return cases


def run_single_case(case: dict, runner: EvalRunner, evaluator: Evaluator, debug: bool = False) -> dict:
    """Run one eval case: sandbox → agent → evaluate → cleanup."""
    case_id = case["case_id"]
    prompt = case["prompt"]
    case_dir = case["case_dir"]

    if debug:
        print(f"\n{'='*60}")
        print(f"Running: {case_id}")
        print(f"{'='*60}")

    sandbox = Sandbox(case_dir)

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
            "tool_stats": result.get("tool_stats", {}),
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
            "tool_stats": {},
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
    parser.add_argument("--max-rounds", type=int, default=None, help="Override max rounds per case (default: from meta.yaml)")
    parser.add_argument("--difficulty", help="Filter by difficulty level (e.g. L1, L2)")
    parser.add_argument("--system-prompt", help="Path to a custom system prompt file (.md/.txt)")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    # Load cases from datasets/
    cases = load_cases()
    if not cases:
        print("Error: no cases found in datasets/")
        sys.exit(1)

    # Filter by difficulty
    if args.difficulty:
        cases = [c for c in cases if c["meta"].get("difficulty") == args.difficulty]
        if not cases:
            print(f"Error: no cases found with difficulty '{args.difficulty}'")
            sys.exit(1)

    # Filter by case_id
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"Error: case '{args.case}' not found")
            sys.exit(1)

    # Load custom system prompt if provided
    custom_system_prompt = None
    if args.system_prompt:
        prompt_path = Path(args.system_prompt)
        if not prompt_path.exists():
            print(f"Error: system prompt file not found: {prompt_path}")
            sys.exit(1)
        custom_system_prompt = prompt_path.read_text(encoding="utf-8")
        print(f"[Eval] Using custom system prompt: {prompt_path}")

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
    if args.max_rounds:
        print(f"   Max rounds: {args.max_rounds} (override)")
    else:
        print(f"   Max rounds: per case meta.yaml")
    print()

    results = []
    for case in cases:
        # Use meta.yaml max_rounds if not overridden via CLI
        case_max_rounds = args.max_rounds or case["meta"].get("max_rounds", 10)
        runner.max_rounds = case_max_rounds

        result = run_single_case(case, runner, evaluator, debug=args.debug)
        results.append(result)

    # Print report
    print()
    print_report(results)

    # Persist results to JSON
    save_results(results, cases, runner, args)


def save_results(results: list[dict], cases: list[dict], runner: EvalRunner, args):
    """Save eval results to a timestamped JSON file."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    # Derive system_prompt_version from filename if provided
    prompt_version = "default"
    if args.system_prompt:
        prompt_version = Path(args.system_prompt).stem

    # Build case meta lookup
    meta_map = {c["case_id"]: c["meta"] for c in cases}

    run_data = {
        "run_id": run_id,
        "config": {
            "model": runner.model,
            "system_prompt_version": prompt_version,
            "max_rounds": args.max_rounds or "per_case",
        },
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1), 4),
            "avg_rounds": round(sum(r["rounds"] for r in results) / max(total, 1), 1),
            "avg_tokens": round(sum(r["total_tokens"] for r in results) / max(total, 1), 0),
        },
        "details": [
            {
                "case_id": r["case_id"],
                "difficulty": meta_map.get(r["case_id"], {}).get("difficulty", ""),
                "category": meta_map.get(r["case_id"], {}).get("category", ""),
                "status": "pass" if r["passed"] else "fail",
                "rounds_used": r["rounds"],
                "total_tokens": r["total_tokens"],
                "error_msg": r["error"],
                "timed_out": r.get("timed_out", False),
                "tool_stats": r.get("tool_stats", {}),
            }
            for r in results
        ],
    }

    # Save to eval_runs/ directory
    runs_dir = Path(__file__).resolve().parent / "eval_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_path = runs_dir / f"run_{run_id}.json"
    output_path.write_text(json.dumps(run_data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[Eval] Results saved to: {output_path}")


if __name__ == "__main__":
    main()
