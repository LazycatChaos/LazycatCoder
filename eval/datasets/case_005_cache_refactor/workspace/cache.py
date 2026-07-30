class SimpleCache:
    """A simple in-memory key-value cache."""

    def __init__(self):
        self._store = {}

    def get(self, key):
        """Get a value from the cache. Returns None if not found."""
        return self._store.get(key)

    def set(self, key, value):
        """Set a value in the cache."""
        self._store[key] = value

    def delete(self, key):
        """Delete a key from the cache."""
        self._store.pop(key, None)

    def clear(self):
        """Clear all items from the cache."""
        self._store.clear()

    def __len__(self):
        return len(self._store)

    def __contains__(self, key):
        return key in self._store

    def keys(self):
        return list(self._store.keys())
