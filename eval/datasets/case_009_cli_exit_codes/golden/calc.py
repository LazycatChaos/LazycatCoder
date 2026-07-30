#!/usr/bin/env python3
"""Simple command-line calculator with proper exit codes and output streams."""

import sys


def calculate(expression: str) -> float:
    """Evaluate a math expression."""
    # Only allow safe characters
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expression):
        raise ValueError(f"Invalid characters in expression: {expression}")
    return eval(expression)


def main():
    if len(sys.argv) < 2:
        print("Usage: python calc.py <expression>", file=sys.stderr)
        print("Example: python calc.py '1 + 2'", file=sys.stderr)
        sys.exit(2)

    expression = sys.argv[1]

    if expression == "--help":
        print("Usage: python calc.py <expression>")
        print("Example: python calc.py '1 + 2'")
        print("Supports: +, -, *, /")
        sys.exit(0)

    try:
        result = calculate(expression)
        print(result)
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
