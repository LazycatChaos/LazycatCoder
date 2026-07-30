import sqlite3
import pytest
from inventory import deduct_stock, get_stock


@pytest.fixture
def db_conn(tmp_path):
    """Create a test database with inventory table."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE inventory (
            sku TEXT PRIMARY KEY,
            stock INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    yield conn
    conn.close()


class TestBasicDeduction:
    """Basic stock deduction tests."""

    def test_deduct_stock_success(self, db_conn):
        """Should deduct stock when sufficient."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 10)")
        db_conn.commit()

        result = deduct_stock(db_conn, 'SKU-001', 3)
        assert result is True

        stock = get_stock(db_conn, 'SKU-001')
        assert stock == 7

    def test_deduct_stock_insufficient(self, db_conn):
        """Should return False when insufficient stock."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 2)")
        db_conn.commit()

        result = deduct_stock(db_conn, 'SKU-001', 5)
        assert result is False

        stock = get_stock(db_conn, 'SKU-001')
        assert stock == 2  # Unchanged

    def test_deduct_stock_nonexistent_sku(self, db_conn):
        """Should return False for non-existent SKU."""
        result = deduct_stock(db_conn, 'SKU-999', 1)
        assert result is False

    def test_deduct_exact_stock(self, db_conn):
        """Should succeed when deducting exact stock amount."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 5)")
        db_conn.commit()

        result = deduct_stock(db_conn, 'SKU-001', 5)
        assert result is True

        stock = get_stock(db_conn, 'SKU-001')
        assert stock == 0

    def test_get_stock_nonexistent(self, db_conn):
        """Should return None for non-existent SKU."""
        assert get_stock(db_conn, 'SKU-999') is None
