import subprocess
import sys

import pytest


CALC_SCRIPT = "calc.py"


def run_calc(args=None):
    """Run the calculator script and return CompletedProcess."""
    cmd = [sys.executable, CALC_SCRIPT]
    if args:
        cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_success_exit_code_zero():
    """Successful calculation should exit with code 0."""
    result = run_calc(["1 + 2"])
    assert result.returncode == 0


def test_result_on_stdout():
    """Result should be on stdout."""
    result = run_calc(["10 * 5"])
    assert result.returncode == 0
    assert "50" in result.stdout
    assert result.stdout.strip() == "50"


def test_runtime_error_exit_code_one():
    """Runtime errors (e.g. division by zero) should exit with code 1."""
    result = run_calc(["1 / 0"])
    assert result.returncode == 1


def test_no_args_exit_code_two():
    """No arguments should exit with code 2."""
    result = run_calc()
    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert len(result.stderr) > 0


def test_help_exit_code_zero():
    """--help should exit with code 0."""
    result = run_calc(["--help"])
    assert result.returncode == 0
    assert "Usage" in result.stdout
