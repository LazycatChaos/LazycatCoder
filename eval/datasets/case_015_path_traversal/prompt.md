# Path Traversal Fix

You are maintaining a file-reading service that allows users to read files from a designated directory.

## Current Code

`file_reader.py` has a `read_user_file(base_dir, filename)` function that reads files.

## Problem

The current implementation is vulnerable to **path traversal attacks**. An attacker can use `../` sequences to escape the allowed directory and read arbitrary files on the system.

## Requirements

1. Prevent any path traversal that would escape `base_dir`.
2. Use `os.path.realpath` (or equivalent) to resolve symlinks and `..` components before checking.
   - Symlinks pointing to files inside `base_dir` should be allowed.
   - Symlinks pointing outside `base_dir` should be blocked.
3. Raise `ValueError` with a descriptive message if traversal is detected.
4. Raise `FileNotFoundError` if the file does not exist.
5. Support reading files in subdirectories of `base_dir`.
6. Handle both forward slashes and backslashes in paths.
7. Handle edge cases like null bytes and encoded traversal sequences safely
   (block or return `FileNotFoundError`).
8. Do not change the function name or module name.

## Example

```python
# Allowed:
read_user_file("/data/uploads", "report.pdf")        # OK
read_user_file("/data/uploads", "docs/readme.txt")   # OK

# Blocked:
read_user_file("/data/uploads", "../etc/passwd")     # ValueError
read_user_file("/data/uploads", "/etc/shadow")       # ValueError or OSError
```

## Notes

- Only use Python standard library modules.
- The fix should be robust against symlink-based escapes as well.
