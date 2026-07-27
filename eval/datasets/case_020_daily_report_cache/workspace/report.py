"""Daily sales report generation.

Generates daily sales reports from order data.
Reports are cached to avoid recomputing for the same date.
"""

from datetime import datetime, timezone
from config import TIMEZONE, CACHE_TTL, REPORT_DATE_FORMAT
from cache_utils import get_cache, set_cache


def get_orders_for_date(date_str):
    """Simulate fetching orders for a given date.

    In production, this queries the database.
    For testing, we use a mock data source.

    Args:
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        List of order dicts with 'amount' and 'date' fields.
    """
    # This function is mocked in tests
    raise NotImplementedError("Override in tests or use mock data")


def generate_daily_report(date_str, orders=None):
    """Generate a daily sales report for the given date.

    Results are cached to avoid recomputation.

    Args:
        date_str: Date string in YYYY-MM-DD format.
        orders: Optional list of order dicts. If None, fetched from data source.

    Returns:
        Dict with 'date', 'total_sales', 'order_count', 'generated_at'.
    """
    cache_key = f"daily_report:{date_str}"

    # Check cache first
    cached = get_cache(cache_key)
    if cached is not None:
        return cached

    # Fetch orders if not provided
    if orders is None:
        orders = get_orders_for_date(date_str)

    # Calculate report
    total_sales = sum(order["amount"] for order in orders)
    order_count = len(orders)

    report = {
        "date": date_str,
        "total_sales": round(total_sales, 2),
        "order_count": order_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Cache the result
    set_cache(cache_key, report, CACHE_TTL)

    return report


def get_report_date():
    """Get the current report date based on system timezone.

    Returns:
        Date string in YYYY-MM-DD format.
    """
    now = datetime.now(timezone.utc)
    return now.strftime(REPORT_DATE_FORMAT)
