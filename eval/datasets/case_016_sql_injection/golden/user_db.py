"""User database query module."""

import sqlite3


def get_user(db_path: str, username: str) -> dict | None:
    """Look up a user by username.

    Args:
        db_path: Path to the SQLite database file.
        username: The username to look up.

    Returns:
        A dict with user data if found, None otherwise.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def search_users(db_path: str, keyword: str) -> list[dict]:
    """Search users by username keyword.

    Args:
        db_path: Path to the SQLite database file.
        keyword: The search keyword (matched with LIKE).

    Returns:
        A list of dicts with matching user data.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Escape LIKE wildcards so they are treated literally
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + escaped + "%"
    cursor.execute(
        "SELECT * FROM users WHERE username LIKE ? ESCAPE '\\'",
        (pattern,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
