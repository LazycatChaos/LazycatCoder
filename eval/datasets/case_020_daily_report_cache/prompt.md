# Fix Cache Expiration Bug Causing Stale Daily Reports

You are fixing a daily sales report system that serves stale data.

## Current Code

The system has three modules:
- `config.py` — system configuration (timezone, cache TTL, date format)
- `cache_utils.py` — in-memory cache with TTL-based expiration
- `report.py` — daily report generation with caching

## Problem

Users report that **daily reports show outdated data** even after new orders are placed. The cache TTL is set to 1 hour (`CACHE_TTL = 3600`), but reports from hours ago are still being served.

## Symptoms

1. Reports generated early in the day still appear hours later, even after the TTL should have expired
2. New orders are not reflected in the report
3. No errors are logged — the system appears to work normally
4. The `config.py` timezone setting is correct (UTC)

## Requirements

1. Fix the cache expiration bug so that cached values properly expire after their TTL.

2. Remove silent exception handling that hides the root cause.

3. Use consistent time representations throughout the cache module.

4. Do not change function names, module names, or the public API.

5. Do not modify `config.py` or `report.py` — the bug is in `cache_utils.py`.

6. Only use Python standard library modules.

## Hints

- Look at how `expires_at` is stored in `set_cache()` vs how it's compared in `get_cache()`.
- `time.time()` returns a `float` (Unix timestamp).
- `datetime.now()` returns a `datetime` object.
- Comparing incompatible types in Python raises `TypeError`.
- `except Exception: pass` silently swallows all errors.

## Example

```python
# The bug: storing datetime but comparing with float
# set_cache stores:
expires_at = datetime.now() + timedelta(seconds=ttl_seconds)  # datetime object

# get_cache compares:
if time.time() > entry["expires_at"]:  # float > datetime → TypeError!

# The fix: use time.time() consistently
# set_cache stores:
expires_at = time.time() + ttl_seconds  # float

# get_cache compares:
if time.time() > entry["expires_at"]:  # float > float → works!
```
