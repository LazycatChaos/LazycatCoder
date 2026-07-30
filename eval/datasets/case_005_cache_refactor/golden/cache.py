import time


class SimpleCache:
    """A simple in-memory key-value cache with optional TTL support."""

    def __init__(self):
        self._store = {}  # key -> (value, expiry_time | None)

    def get(self, key):
        """Get a value from the cache. Returns None if not found or expired."""
        if key not in self._store:
            return None
        value, expiry = self._store[key]
        if expiry is not None and time.time() >= expiry:
            del self._store[key]
            return None
        return value

    def set(self, key, value, ttl=None):
        """Set a value in the cache.

        Args:
            key: The cache key.
            value: The value to store.
            ttl: Time-to-live in seconds. None means permanent.
        """
        expiry = time.time() + ttl if ttl is not None else None
        self._store[key] = (value, expiry)

    def delete(self, key):
        """Delete a key from the cache."""
        self._store.pop(key, None)

    def clear(self):
        """Clear all items from the cache."""
        self._store.clear()

    def __len__(self):
        return sum(1 for key in self._store if not self._is_expired(key))

    def __contains__(self, key):
        return key in self._store and not self._is_expired(key)

    def keys(self):
        return [key for key in self._store if not self._is_expired(key)]

    def _is_expired(self, key):
        """Check if a key is expired (does not remove it)."""
        if key not in self._store:
            return True
        _, expiry = self._store[key]
        if expiry is not None and time.time() >= expiry:
            return True
        return False
