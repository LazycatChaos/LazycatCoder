import pytest
from cache import SimpleCache


class TestCacheBackwardCompat:
    """Ensure existing API still works."""

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
