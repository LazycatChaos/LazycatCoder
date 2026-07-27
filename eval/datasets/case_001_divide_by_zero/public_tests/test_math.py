import math_utils


def test_divide_normal():
    """Normal division should work."""
    assert math_utils.divide(10, 2) == 5.0
    assert math_utils.divide(7, 2) == 3.5


def test_safe_divide_normal():
    """safe_divide should work normally when b is not zero."""
    assert math_utils.safe_divide(10, 2) == 5.0
    assert math_utils.safe_divide(7, 3) == 3.5
