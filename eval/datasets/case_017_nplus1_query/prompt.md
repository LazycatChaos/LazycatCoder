# Fix N+1 Query in User Orders Retrieval

You are optimizing a query that fetches users with their orders.

## Current Code

`order_query.py` has a `get_users_with_orders(conn)` function.

## Problem

The current implementation has an **N+1 query problem**:
1. It first queries all users: `SELECT ... FROM users` (1 query)
2. Then for **each user**, it queries their orders: `SELECT ... FROM orders WHERE user_id = ?` (N queries)

For 100 users, this means **101 database queries**. This is extremely inefficient.

## Requirements

1. Reduce the query count to **at most 3 queries** (ideally 1 with a JOIN).
2. Use a `JOIN` or `IN` clause to batch-fetch all orders in a single query.
3. Group orders by user in Python after fetching.
4. Return the **exact same data structure** as before:
   ```python
   [
       {"user": {"id": 1, "username": "alice", "email": "..."}, "orders": [...]},
       ...
   ]
   ```
5. Users without orders should have an empty `orders` list.
6. Do not change the function name, module name, or signature.

## Example

```python
# Before (N+1):
# Query 1: SELECT * FROM users
# Query 2: SELECT * FROM orders WHERE user_id = 1
# Query 3: SELECT * FROM orders WHERE user_id = 2
# ... (N+1 total)

# After (optimized):
# Query 1: SELECT u.*, o.* FROM users u LEFT JOIN orders o ON u.id = o.user_id
# (1 query total)
```

## Notes

- The test suite uses `sqlite3.Connection.set_trace_callback()` to count actual SQL queries executed.
- Your solution must execute **≤ 3 queries** regardless of how many users exist.
- Only use Python standard library modules.
