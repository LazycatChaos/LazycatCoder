# CLI: Fix Exit Codes and Output Streams

## Background

We have a simple command-line calculator tool that evaluates math expressions:

```bash
python calc.py "1 + 2"
```

Currently, the tool has several issues:
- Success and failure both exit with code 0
- Error messages go to stdout instead of stderr
- Invalid arguments don't have a clear exit code

## Task

Fix the CLI tool to use proper exit codes and output streams.

## Requirements

1. **Success**: Exit code 0, result printed to stdout.
   - stdout must contain **only the raw result value**, with no prefix, suffix,
     or decoration.
   - Example: `calc.py "25 + 25"` → stdout is `50` (not `Result: 50`).
   - Note: Remove the "Result: " prefix from the current workspace implementation.
2. **Runtime error** (e.g. division by zero, invalid expression): Exit code 1,
   error message printed to stderr. stdout must be empty.
3. **Invalid arguments** (e.g. no expression provided): Exit code 2,
   usage message printed to stderr. stdout must be empty.
4. **--help**: Must be available, exit code 0.
5. **Clean stdout**: Normal results on stdout must NOT contain any log messages
   or error text.

## What NOT to change

- The tool should still accept a single expression string as argument.
- The calculator should support basic operations: +, -, *, /.
