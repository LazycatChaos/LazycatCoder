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
    # stdout should only contain the result, not "Result:" prefix
    assert result.stdout.strip() == "50"


def test_error_message_on_stderr():
    """Error messages should go to stderr, not stdout."""
    result = run_calc(["1 / 0"])
    assert result.returncode == 1
    assert "Error" in result.stderr or "division" in result.stderr.lower()
    assert result.stdout.strip() == ""


def test_runtime_error_exit_code_one():
    """Runtime errors (e.g. division by zero) should exit with code 1."""
    result = run_calc(["1 / 0"])
    assert result.returncode == 1


def test_invalid_expression_exit_code_one():
    """Invalid expressions should exit with code 1."""
    result = run_calc(["abc + def"])
    assert result.returncode == 1
    assert result.stdout.strip() == ""


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


def test_complex_expression():
    """Complex expressions should work correctly."""
    result = run_calc(["(2 + 3) * 4"])
    assert result.returncode == 0
    assert result.stdout.strip() == "20"


def test_float_result():
    """Float results should be correct."""
    result = run_calc(["10 / 3"])
    assert result.returncode == 0
    assert abs(float(result.stdout.strip()) - 10 / 3) < 0.001


def test_negative_result():
    """Negative results should work."""
    result = run_calc(["5 - 10"])
    assert result.returncode == 0
    assert result.stdout.strip() == "-5"
