# Webhook Signature Verification

You are working on a webhook receiver that needs to verify incoming request signatures.

## Current Code

`webhook.py` has a basic `verify_webhook` function that compares signatures using `==`.

## Problem

The current implementation has several security issues:

1. **No replay protection** — an attacker can capture a valid signature and replay it indefinitely.
2. **Timing attack vulnerability** — using `==` for signature comparison leaks timing information.
3. **No timestamp validation** — there is no way to reject stale requests.

## Requirements

1. Use **HMAC-SHA256** for signature computation.
2. Signature header format: `t=<timestamp>,v1=<signature>` where timestamp is
   a Unix epoch integer. Spaces around `=` are optional (e.g. `t = 123, v1 = abc`).
3. Support **timestamp tolerance** (default 300 seconds). Reject requests outside this window.
4. Must use **constant-time comparison** (`hmac.compare_digest`) for signature verification.
5. The signed payload must include the timestamp to prevent replay attacks.
6. Missing or malformed headers should return `False` (not raise exceptions).
7. Do not change the function name or module name.

## Example Header

```
t=1705312200,v1=a1b2c3d4e5f6...
```

## Notes

- The `compute_signature` helper is provided. You may modify it.
- Only use Python standard library modules.
