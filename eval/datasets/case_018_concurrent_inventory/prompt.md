# Fix Race Condition in Concurrent Stock Deduction

You are fixing a stock deduction service for an e-commerce platform.

## Current Code

`inventory.py` has two functions:
- `deduct_stock(conn, sku, quantity)` — deduct stock for a product
- `get_stock(conn, sku)` — get current stock level

## Problem

The current implementation has a **race condition** (read-check-write pattern):
1. `SELECT stock FROM inventory WHERE sku = ?` — reads current stock
2. Checks if `stock >= quantity` in Python
3. `UPDATE inventory SET stock = stock - ? WHERE sku = ?` — writes new stock

When two threads/processes execute this simultaneously, they can both read the same stock value and both succeed, causing **overselling** (stock goes negative).

## Requirements

1. Use an **atomic UPDATE** with a WHERE guard:
   ```sql
   UPDATE inventory SET stock = stock - ? WHERE sku = ? AND stock >= ?
   ```
   This ensures the check and update happen in a single atomic operation.

2. Use `cursor.rowcount` to determine if the deduction succeeded (1 row updated) or failed (0 rows updated).

3. **Commit** the transaction after the UPDATE.

4. Do not change function names, module name, or return types.

5. Only use Python standard library modules.

## Example

```python
# Before (vulnerable — read-check-write race):
row = conn.execute("SELECT stock FROM inventory WHERE sku = ?", (sku,)).fetchone()
if row[0] >= quantity:
    conn.execute("UPDATE inventory SET stock = stock - ? WHERE sku = ?", (quantity, sku))
    return True
return False

# After (safe — atomic UPDATE):
cursor = conn.execute(
    "UPDATE inventory SET stock = stock - ? WHERE sku = ? AND stock >= ?",
    (quantity, sku, quantity),
)
conn.commit()
return cursor.rowcount > 0
```

## Notes

- The `inventory` table has columns: `sku` (TEXT PRIMARY KEY), `stock` (INTEGER).
- `cursor.rowcount` returns the number of rows affected by the last statement.
- This pattern works correctly even under high concurrency without explicit locks.
