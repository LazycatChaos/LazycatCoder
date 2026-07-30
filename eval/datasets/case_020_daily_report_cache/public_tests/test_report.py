import pytest
from report import generate_daily_report, get_report_date
from cache_utils import set_cache, get_cache, clear_cache, cache_size


@pytest.fixture(autouse=True)
def clean_cache():
    """Clear cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


class TestBasicReport:
    """Basic report generation tests."""

    def test_generate_report(self):
        """Should generate a report for given orders."""
        orders = [
            {"amount": 100.00, "date": "2024-01-15"},
            {"amount": 200.50, "date": "2024-01-15"},
        ]
        report = generate_daily_report("2024-01-15", orders=orders)

        assert report["date"] == "2024-01-15"
        assert report["total_sales"] == 300.50
        assert report["order_count"] == 2
        assert "generated_at" in report

    def test_generate_report_empty_orders(self):
        """Should handle empty order list."""
        report = generate_daily_report("2024-01-15", orders=[])

        assert report["date"] == "2024-01-15"
        assert report["total_sales"] == 0
        assert report["order_count"] == 0

    def test_report_date_format(self):
        """get_report_date should return YYYY-MM-DD format."""
        date_str = get_report_date()
        assert len(date_str) == 10
        assert date_str[4] == "-"
        assert date_str[7] == "-"


class TestCaching:
    """Basic caching behavior tests."""

    def test_cache_set_and_get(self):
        """Should store and retrieve cached values."""
        set_cache("test_key", {"data": "value"}, 60)
        result = get_cache("test_key")
        assert result == {"data": "value"}

    def test_cache_miss(self):
        """Should return None for missing keys."""
        assert get_cache("nonexistent") is None

    def test_cache_clear(self):
        """Should clear all cache entries."""
        set_cache("key1", "value1", 60)
        set_cache("key2", "value2", 60)
        clear_cache()
        assert cache_size() == 0

    def test_cache_size(self):
        """Should report correct cache size."""
        set_cache("a", 1, 60)
        set_cache("b", 2, 60)
        assert cache_size() == 2
