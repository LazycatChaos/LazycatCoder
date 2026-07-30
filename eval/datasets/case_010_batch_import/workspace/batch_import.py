"""Batch import module for user data."""

import sqlite3


def batch_import_users(conn: sqlite3.Connection, users: list[dict], continue_on_error: bool = False) -> dict:
    """Import a batch of users into the database.

    Args:
        conn: An open sqlite3 connection.
        users: List of dicts with keys: username, email, role.
        continue_on_error: If True, skip failed rows and continue.
                          If False (default), rollback all on first error.

    Returns:
        A dict with keys:
          - 'imported': number of successfully imported rows
          - 'errors': list of error messages (empty if continue_on_error is False)
    """
    imported = 0
    errors = []

    for user in users:
        try:
            conn.execute(
                "INSERT INTO users (username, email, role) VALUES (?, ?, ?)",
                (user["username"], user["email"], user.get("role", "user")),
            )
            imported += 1
        except Exception as e:
            if continue_on_error:
                errors.append(f"Failed to import {user.get('username', '?')}: {e}")
            else:
                raise

    return {"imported": imported, "errors": errors}
