"""Cache utilities for daily report system.

Provides a simple in-memory cache with TTL-based expiration.
"""

import time

# In-memory cache store
_cache = {}


def set_cache(key, value, ttl_seconds):
    """Store a value in the cache with a TTL.

    Args:
        key: Cache key string.
        value: Any serializable value.
        ttl_seconds: Time-to-live in seconds.
    """
    expires_at = time.time() + ttl_seconds
    _cache[key] = {
        "value": value,
        "expires_at": expires_at,
    }


def get_cache(key):
    """Get a value from the cache, returning None if expired or missing.

    Args:
        key: Cache key string.

    Returns:
        Cached value, or None if not found or expired.
    """
    if key not in _cache:
        return None
    entry = _cache[key]
    if time.time() > entry["expires_at"]:
        del _cache[key]
        return None
    return entry["value"]


def invalidate_cache():
    """Remove all expired entries from the cache."""
    expired_keys = []
    for key, entry in _cache.items():
        if time.time() > entry["expires_at"]:
            expired_keys.append(key)
    for key in expired_keys:
        del _cache[key]


def clear_cache():
    """Clear all cache entries."""
    _cache.clear()


def cache_size():
    """Return the number of entries in the cache."""
    return len(_cache)
