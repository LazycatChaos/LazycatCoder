"""Secure file reader that prevents path traversal attacks."""

import os


def read_user_file(base_dir: str, filename: str) -> str:
    """Read a file from the user-accessible directory.

    Args:
        base_dir: The root directory that users are allowed to access.
        filename: The name of the file to read (relative to base_dir).

    Returns:
        The file contents as a string.

    Raises:
        ValueError: If the file path attempts to escape base_dir.
        FileNotFoundError: If the file does not exist.
    """
    # Normalize the base directory
    base_dir = os.path.realpath(base_dir)

    # Construct the full path
    full_path = os.path.join(base_dir, filename)
    full_path = os.path.realpath(full_path)

    # Check that the resolved path is still within base_dir
    if not full_path.startswith(base_dir + os.sep) and full_path != base_dir:
        raise ValueError(
            f"Access denied: '{filename}' attempts to escape the allowed directory"
        )

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
