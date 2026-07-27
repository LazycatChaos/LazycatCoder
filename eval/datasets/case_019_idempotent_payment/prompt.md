# Fix Payment Idempotency with Database-Level Deduplication

You are fixing a payment processing service for an e-commerce platform.

## Current Code

`payment.py` has:
- `init_db(db_path)` — initializes the payments database
- `process_payment(conn, request)` — processes a payment with idempotency check
- `PaymentRequest` — dataclass with `idempotency_key`, `amount`, `currency`, `description`
- `PaymentResult` — dataclass with `status_code` and `body`
- `PaymentStatus` — enum: `pending`, `success`, `failed`

## Problem

The current implementation uses an **in-memory dict** (`_idempotency_cache`) to track processed payments. This is fragile:

1. **Service restart loses all idempotency state** — duplicate payments will be processed
2. **No database-level protection** — concurrent requests can both pass the cache check
3. **No handling for `IntegrityError`** — if a UNIQUE constraint is added, the code will crash

## Requirements

1. Add a **UNIQUE constraint** on `idempotency_key` in the `payments` table schema.

2. Use **database-level idempotency**: attempt the INSERT, and if it fails with `sqlite3.IntegrityError`, look up the existing record and return its result.

3. Handle three states for existing records:
   - **`success`** → return 200 with `body` containing `"status": "success"` and `"message": "Payment already processed"`
   - **`pending`** → return 202 with `body` containing `"status": "pending"` and `"message": "Payment still processing, please retry later"`
   - **`failed`** → allow retry by updating the record to `pending` and reprocessing

4. **Do not** use in-memory dict/cache for idempotency.

5. Do not change function names, module name, or return types.

6. Only use Python standard library modules.

## Example

```python
# Before (vulnerable — in-memory cache):
if request.idempotency_key in _idempotency_cache:
    return cached_result  # Lost on restart!

# After (safe — database-level):
try:
    conn.execute("INSERT INTO payments ... VALUES (?, ?, ...)", (...))
    conn.commit()
except sqlite3.IntegrityError:
    # Key already exists — return existing result
    row = conn.execute(
        "SELECT id, status FROM payments WHERE idempotency_key = ?",
        (request.idempotency_key,),
    ).fetchone()
    # ... handle based on status ...
```

## Notes

- `sqlite3.IntegrityError` is raised when a UNIQUE constraint is violated.
- The `idempotency_key` is provided by the client to ensure exactly-once processing.
- A `pending` status means the payment is still being processed (e.g., waiting for external gateway).
- A `failed` status means the previous attempt failed — the client may retry.
