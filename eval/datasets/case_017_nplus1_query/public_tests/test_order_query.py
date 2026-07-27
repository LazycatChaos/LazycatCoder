import sqlite3
import pytest
from order_query import get_users_with_orders


@pytest.fixture
def db_conn(tmp_path):
    """Create a test database with users and orders."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            email TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    # Insert test data
    conn.executemany(
        "INSERT INTO users (id, username, email) VALUES (?, ?, ?)",
        [
            (1, "alice", "alice@example.com"),
            (2, "bob", "bob@example.com"),
            (3, "charlie", "charlie@example.com"),
        ],
    )
    conn.executemany(
        "INSERT INTO orders (id, user_id, product, amount) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "Widget", 9.99),
            (2, 1, "Gadget", 19.99),
            (3, 2, "Doohickey", 4.99),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


class TestBasicQuery:
    """Basic query tests."""

    def test_returns_all_users(self, db_conn):
        """Should return all users."""
        result = get_users_with_orders(db_conn)
        assert len(result) == 3

    def test_user_has_orders(self, db_conn):
        """Users with orders should have them listed."""
        result = get_users_with_orders(db_conn)
        alice = next(u for u in result if u["user"]["username"] == "alice")
        assert len(alice["orders"]) == 2

    def test_user_without_orders(self, db_conn):
        """Users without orders should have empty orders list."""
        result = get_users_with_orders(db_conn)
        charlie = next(u for u in result if u["user"]["username"] == "charlie")
        assert charlie["orders"] == []

    def test_order_data_correct(self, db_conn):
        """Order data should be correct."""
        result = get_users_with_orders(db_conn)
        bob = next(u for u in result if u["user"]["username"] == "bob")
        assert len(bob["orders"]) == 1
        assert bob["orders"][0]["product"] == "Doohickey"
        assert bob["orders"][0]["amount"] == 4.99
