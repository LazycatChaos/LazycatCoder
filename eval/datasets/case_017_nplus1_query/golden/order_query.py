"""User order query module."""

import sqlite3


def get_users_with_orders(conn: sqlite3.Connection) -> list[dict]:
    """Get all users with their orders.

    Args:
        conn: An open sqlite3 connection.

    Returns:
        A list of dicts, each with 'user' and 'orders' keys.
    """
    # Use a single JOIN query instead of N+1
    rows = conn.execute("""
        SELECT u.id as user_id, u.username, u.email,
               o.id as order_id, o.product, o.amount
        FROM users u
        LEFT JOIN orders o ON u.id = o.user_id
        ORDER BY u.id
    """).fetchall()

    # Group by user
    result = []
    current_user = None
    current_orders = []

    for row in rows:
        if current_user is None or row["user_id"] != current_user["id"]:
            if current_user is not None:
                result.append({
                    "user": current_user,
                    "orders": current_orders,
                })
            current_user = {
                "id": row["user_id"],
                "username": row["username"],
                "email": row["email"],
            }
            current_orders = []

        if row["order_id"] is not None:
            current_orders.append({
                "id": row["order_id"],
                "product": row["product"],
                "amount": row["amount"],
            })

    if current_user is not None:
        result.append({
            "user": current_user,
            "orders": current_orders,
        })

    return result
