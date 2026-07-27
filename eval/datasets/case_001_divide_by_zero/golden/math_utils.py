def divide(a, b):
    """Divide a by b."""
    return a / b


def safe_divide(a, b):
    """Divide a by b, returning 0 if b is zero."""
    if b == 0:
        return 0
    return a / b
