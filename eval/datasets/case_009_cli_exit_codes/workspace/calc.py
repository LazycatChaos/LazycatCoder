#!/usr/bin/env python3
"""Simple command-line calculator."""

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
        print("Usage: python calc.py <expression>")
        print("Example: python calc.py '1 + 2'")
        return

    expression = sys.argv[1]

    if expression == "--help":
        print("Usage: python calc.py <expression>")
        print("Example: python calc.py '1 + 2'")
        print("Supports: +, -, *, /")
        return

    try:
        result = calculate(expression)
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
