"""Inventory stock deduction module."""

import sqlite3


def deduct_stock(conn: sqlite3.Connection, sku: str, quantity: int) -> bool:
    """Deduct stock for a given SKU.

    Uses an atomic UPDATE with a WHERE clause to prevent race conditions.

    Args:
        conn: An open sqlite3 connection.
        sku: The product SKU.
        quantity: The quantity to deduct.

    Returns:
        True if deduction succeeded, False if insufficient stock.
    """
    cursor = conn.execute(
        "UPDATE inventory SET stock = stock - ? WHERE sku = ? AND stock >= ?",
        (quantity, sku, quantity),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_stock(conn: sqlite3.Connection, sku: str) -> int | None:
    """Get current stock for a SKU.

    Args:
        conn: An open sqlite3 connection.
        sku: The product SKU.

    Returns:
        Current stock level, or None if SKU not found.
    """
    row = conn.execute(
        "SELECT stock FROM inventory WHERE sku = ?",
        (sku,),
    ).fetchone()
    return row[0] if row else None
