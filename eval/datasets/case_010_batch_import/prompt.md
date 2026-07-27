# Batch Import with Transaction Support

You are building a batch import feature for a user management system.

## Current Code

`batch_import.py` has a `batch_import_users(conn, users, continue_on_error=False)` function that inserts users one by one.

## Problem

The current implementation has **no transaction management**:
1. If an error occurs mid-batch, previously inserted rows remain in the database (partial state).
2. There is no `continue_on_error` mode — it just raises on the first error.
3. The caller has no way to know which rows failed when using partial mode.

## Requirements

1. **Atomic mode** (`continue_on_error=False`, the default):
   - Wrap all inserts in a transaction.
   - If **any** row fails, **rollback** the entire batch.
   - If all succeed, **commit**.

2. **Partial mode** (`continue_on_error=True`):
   - Skip failed rows and continue processing.
   - **Commit** all successful inserts.
   - Return a list of error messages for failed rows.

3. **Return value**: Always return a dict with:
   - `'imported'`: number of successfully imported rows
   - `'errors'`: list of error messages (empty in atomic mode since it raises)

4. Use parameterized queries (`?` placeholders) — no string interpolation.

5. Do not change the function name, module name, or signature.

## Example

```python
# Atomic mode (default) — all or nothing
result = batch_import_users(conn, users)
# If any row fails, an exception is raised and nothing is committed.

# Partial mode — skip failures
result = batch_import_users(conn, users, continue_on_error=True)
# Returns: {"imported": 8, "errors": ["Failed to import bob: UNIQUE constraint failed"]}
```

## Notes

- The `users` list contains dicts with keys: `username`, `email`, and optionally `role` (default: `"user"`).
- The `users` table has a `UNIQUE` constraint on `username`.
- Only use Python standard library modules.
