"""Insecure file reader — vulnerable to path traversal."""

import os


def read_user_file(base_dir: str, filename: str) -> str:
    """Read a file from the user-accessible directory.

    WARNING: This implementation is vulnerable to path traversal attacks.
    """
    full_path = os.path.join(base_dir, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()
