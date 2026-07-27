"""User order query module."""

import sqlite3


def get_users_with_orders(conn: sqlite3.Connection) -> list[dict]:
    """Get all users with their orders.

    Args:
        conn: An open sqlite3 connection.

    Returns:
        A list of dicts, each with 'user' and 'orders' keys.
    """
    users = conn.execute("SELECT id, username, email FROM users").fetchall()

    result = []
    for user in users:
        orders = conn.execute(
            "SELECT id, product, amount FROM orders WHERE user_id = ?",
            (user["id"],),
        ).fetchall()
        result.append({
            "user": dict(user),
            "orders": [dict(o) for o in orders],
        })

    return result
