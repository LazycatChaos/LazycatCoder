import math_utils
import pytest


def test_divide_normal():
    """Normal division should work."""
    assert math_utils.divide(10, 2) == 5.0
    assert math_utils.divide(7, 2) == 3.5


def test_safe_divide_by_zero():
    """safe_divide should return 0 when dividing by zero, not raise ZeroDivisionError."""
    result = math_utils.safe_divide(10, 0)
    assert result == 0, f"Expected 0, got {result}"


def test_safe_divide_normal():
    """safe_divide should work normally when b is not zero."""
    assert math_utils.safe_divide(10, 2) == 5.0
    assert math_utils.safe_divide(7, 3) == pytest.approx(7 / 3)
