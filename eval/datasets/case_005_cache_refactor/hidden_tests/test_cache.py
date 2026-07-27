import pytest
import time
from cache import SimpleCache


class TestCacheBackwardCompat:
    """Ensure existing API still works after TTL refactor."""

    def test_basic_set_get(self):
        c = SimpleCache()
        c.set("key", "value")
        assert c.get("key") == "value"

    def test_get_missing(self):
        c = SimpleCache()
        assert c.get("missing") is None

    def test_delete(self):
        c = SimpleCache()
        c.set("key", "value")
        c.delete("key")
        assert c.get("key") is None

    def test_clear(self):
        c = SimpleCache()
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert len(c) == 0

    def test_len(self):
        c = SimpleCache()
        c.set("a", 1)
        c.set("b", 2)
        assert len(c) == 2

    def test_contains(self):
        c = SimpleCache()
        c.set("key", "value")
        assert "key" in c
        assert "missing" not in c

    def test_keys(self):
        c = SimpleCache()
        c.set("a", 1)
        c.set("b", 2)
        assert sorted(c.keys()) == ["a", "b"]

    def test_overwrite(self):
        c = SimpleCache()
        c.set("key", "old")
        c.set("key", "new")
        assert c.get("key") == "new"


class TestCacheTTL:
    """Test new TTL functionality."""

    def test_ttl_expiry(self):
        c = SimpleCache()
        c.set("key", "value", ttl=0.1)
        assert c.get("key") == "value"
        time.sleep(0.15)
        assert c.get("key") is None

    def test_ttl_no_expiry(self):
        c = SimpleCache()
        c.set("key", "value", ttl=None)
        time.sleep(0.05)
        assert c.get("key") == "value"

    def test_ttl_default_is_permanent(self):
        """set(key, value) without ttl should be permanent."""
        c = SimpleCache()
        c.set("key", "value")
        time.sleep(0.05)
        assert c.get("key") == "value"

    def test_ttl_different_keys(self):
        c = SimpleCache()
        c.set("fast", "expire", ttl=0.1)
        c.set("slow", "stay", ttl=10)
        time.sleep(0.15)
        assert c.get("fast") is None
        assert c.get("slow") == "stay"

    def test_ttl_len_excludes_expired(self):
        c = SimpleCache()
        c.set("a", 1, ttl=0.1)
        c.set("b", 2, ttl=None)
        time.sleep(0.15)
        assert len(c) == 1

    def test_ttl_contains_excludes_expired(self):
        c = SimpleCache()
        c.set("key", "value", ttl=0.1)
        time.sleep(0.15)
        assert "key" not in c

    def test_ttl_keys_excludes_expired(self):
        c = SimpleCache()
        c.set("a", 1, ttl=0.1)
        c.set("b", 2, ttl=None)
        time.sleep(0.15)
        assert c.keys() == ["b"]

    def test_ttl_delete_expired(self):
        """Deleting an expired key should not raise."""
        c = SimpleCache()
        c.set("key", "value", ttl=0.1)
        time.sleep(0.15)
        c.delete("key")  # Should not raise
