# SQL Injection Fix

You are maintaining a user lookup service backed by SQLite.

## Current Code

`user_db.py` has two functions:
- `get_user(db_path, username)` — look up a single user by username
- `search_users(db_path, keyword)` — search users by username keyword (LIKE match)

## Problem

Both functions construct SQL queries using **f-string interpolation**, making them vulnerable to **SQL injection attacks**. An attacker can:

1. Bypass authentication by injecting `OR '1'='1` conditions.
2. Extract data from other tables using `UNION SELECT`.
3. Modify or delete data using stacked queries (`; DROP TABLE ...`).

## Requirements

1. Use **parameterized queries** (`?` placeholders) for all SQL execution.
2. Never use f-strings, `.format()`, or `%` formatting to build SQL queries.
3. For `search_users`, escape LIKE wildcards (`%` and `_`) so they are treated as literal characters.
   An empty keyword should return all users (match everything).
4. Do not change function names, module name, or return types.
5. Only use Python standard library modules.

## Example

```python
# Before (vulnerable):
query = f"SELECT * FROM users WHERE username = '{username}'"
cursor.execute(query)

# After (safe):
cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
```

## Notes

- The database schema includes a `users` table and potentially other tables (e.g., `secrets`).
- Your fix should prevent all forms of SQL injection: UNION-based, comment-based, and stacked queries.
