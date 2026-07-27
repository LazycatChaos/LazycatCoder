import time
import pytest
from report import generate_daily_report, get_report_date
from cache_utils import set_cache, get_cache, clear_cache, cache_size, invalidate_cache


@pytest.fixture(autouse=True)
def clean_cache():
    """Clear cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


class TestCacheExpiration:
    """Tests for cache TTL expiration — the core bug area."""

    def test_cache_expires_after_ttl(self):
        """Cached values should expire after TTL seconds.

        BUG: The workspace stores expires_at as a datetime object but
        compares it with time.time() (a float). This raises TypeError,
        which is silently swallowed by 'except Exception: pass',
        causing the cache to NEVER expire.
        """
        set_cache("expiring_key", "fresh_data", ttl_seconds=1)
        assert get_cache("expiring_key") == "fresh_data"

        # Wait for TTL to expire
        time.sleep(1.5)

        # Should be expired now
        result = get_cache("expiring_key")
        assert result is None, (
            "Cache should have expired after TTL. "
            "Check that expires_at and time.time() use compatible types."
        )

    def test_cache_expiration_triggers_recompute(self):
        """Expired cache should trigger report recomputation.

        If cache doesn't expire properly, stale reports are returned.
        """
        orders_v1 = [
            {"amount": 100.00, "date": "2024-01-15"},
        ]
        # First call — generates and caches
        report1 = generate_daily_report("2024-01-15", orders=orders_v1)
        assert report1["total_sales"] == 100.00

        # Simulate cache expiration by clearing it
        clear_cache()

        # New orders for the same date
        orders_v2 = [
            {"amount": 100.00, "date": "2024-01-15"},
            {"amount": 50.00, "date": "2024-01-15"},
        ]
        report2 = generate_daily_report("2024-01-15", orders=orders_v2)
        assert report2["total_sales"] == 150.00

    def test_cache_still_valid_within_ttl(self):
        """Cache should return valid data within TTL."""
        set_cache("valid_key", "valid_data", ttl_seconds=60)
        assert get_cache("valid_key") == "valid_data"

    def test_invalidate_cache_removes_expired(self):
        """invalidate_cache should remove expired entries."""
        set_cache("expired", "old_data", ttl_seconds=1)
        set_cache("fresh", "new_data", ttl_seconds=3600)

        time.sleep(1.5)
        invalidate_cache()

        assert get_cache("expired") is None
        assert get_cache("fresh") == "new_data"


class TestStaleData:
    """Tests for stale data scenarios."""

    def test_no_stale_data_after_ttl(self):
        """After TTL expires, new data should be computed, not stale data."""
        orders_initial = [{"amount": 10.00, "date": "2024-06-01"}]
        report1 = generate_daily_report("2024-06-01", orders=orders_initial)
        assert report1["total_sales"] == 10.00

        # Clear cache to simulate expiration
        clear_cache()

        orders_updated = [{"amount": 10.00, "date": "2024-06-01"}, {"amount": 20.00, "date": "2024-06-01"}]
        report2 = generate_daily_report("2024-06-01", orders=orders_updated)
        assert report2["total_sales"] == 30.00

    def test_multiple_dates_independent(self):
        """Different dates should have independent cache entries."""
        orders_a = [{"amount": 100.00, "date": "2024-01-01"}]
        orders_b = [{"amount": 200.00, "date": "2024-01-02"}]

        report_a = generate_daily_report("2024-01-01", orders=orders_a)
        report_b = generate_daily_report("2024-01-02", orders=orders_b)

        assert report_a["total_sales"] == 100.00
        assert report_b["total_sales"] == 200.00


class TestSourceInspection:
    """Tests to verify the solution uses proper patterns."""

    def test_expires_at_is_numeric(self):
        """set_cache should store expires_at as a numeric timestamp, not datetime."""
        import inspect
        source = inspect.getsource(set_cache)
        # Should use time.time() for expires_at, not datetime.now()
        assert "time.time()" in source, (
            "set_cache should use time.time() for expires_at to match get_cache comparison"
        )
        # Should NOT use datetime for expires_at
        assert "datetime.now()" not in source, (
            "set_cache should not use datetime.now() for expires_at — "
            "it causes TypeError when compared with time.time()"
        )

    def test_no_silent_exception_swallowing(self):
        """get_cache should not silently swallow exceptions."""
        import inspect
        source = inspect.getsource(get_cache)
        # Should not have bare except Exception: pass
        assert "except Exception" not in source, (
            "get_cache should not use 'except Exception: pass' — "
            "this silently hides type comparison errors"
        )

    def test_no_datetime_import_in_cache_utils(self):
        """cache_utils should not need datetime module."""
        import inspect
        import cache_utils
        source = inspect.getsource(cache_utils)
        assert "from datetime" not in source and "import datetime" not in source, (
            "cache_utils should not import datetime — use time.time() for all time operations"
        )


class TestEdgeCases:
    """Edge case tests."""

    def test_zero_ttl(self):
        """Zero TTL should expire immediately."""
        set_cache("zero_ttl", "data", ttl_seconds=0)
        # With 0 TTL, expires_at == time.time() at set time
        # After even a tiny delay, it should be expired
        time.sleep(0.01)
        assert get_cache("zero_ttl") is None

    def test_very_long_ttl(self):
        """Very long TTL should keep data cached."""
        set_cache("long_ttl", "persistent", ttl_seconds=86400)
        assert get_cache("long_ttl") == "persistent"

    def test_overwrite_existing_key(self):
        """Setting same key should update the value."""
        set_cache("key", "old", ttl_seconds=3600)
        set_cache("key", "new", ttl_seconds=3600)
        assert get_cache("key") == "new"
        assert cache_size() == 1

    def test_cache_with_none_value(self):
        """Cache should distinguish between None value and cache miss."""
        set_cache("none_key", None, ttl_seconds=60)
        # This is a tricky case — get_cache returns None for both
        # miss and stored None. The fix should handle this correctly.
        # For our purposes, we just verify it doesn't crash.
        result = get_cache("none_key")
        assert result is None

    def test_invalidate_empty_cache(self):
        """invalidate_cache on empty cache should not crash."""
        invalidate_cache()  # Should not raise

    def test_clear_empty_cache(self):
        """clear_cache on empty cache should not crash."""
        clear_cache()  # Should not raise
