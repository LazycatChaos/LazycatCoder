"""Evaluator: inject hidden tests into sandbox and run pytest."""

import subprocess
from pathlib import Path


class Evaluator:
    """Run tests against the Agent's modified code in the sandbox.

    The hidden tests should already be injected into the sandbox workdir
    (via Sandbox.inject_tests()) before calling evaluate().
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def evaluate(self, workdir: str | Path) -> dict:
        """Run pytest in the sandbox workdir.

        Returns:
            {
                "passed": bool,        # Did all tests pass?
                "stdout": str,         # pytest stdout
                "stderr": str,         # pytest stderr
                "timed_out": bool,     # Did pytest timeout?
            }
        """
        workdir = Path(workdir)

        # Check if there are any test files
        test_files = list(workdir.glob("test_*.py"))
        if not test_files:
            return {
                "passed": False,
                "stdout": "",
                "stderr": "No test_*.py files found in sandbox",
                "timed_out": False,
            }

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [str(f) for f in test_files],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return {
                "passed": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "stdout": "",
                "stderr": f"Test execution timed out after {self.timeout}s",
                "timed_out": True,
            }
        except Exception as e:
            return {
                "passed": False,
                "stdout": "",
                "stderr": str(e),
                "timed_out": False,
            }


import sys  # noqa: E402 (needed for sys.executable above)
