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
    conn.commit()
    yield conn
    conn.close()


def _setup_data(conn, num_users, orders_per_user):
    """Helper to set up test data."""
    users = [(i, f"user_{i}", f"user_{i}@example.com") for i in range(1, num_users + 1)]
    conn.executemany("INSERT INTO users (id, username, email) VALUES (?, ?, ?)", users)

    orders = []
    order_id = 1
    for user_id in range(1, num_users + 1):
        for j in range(orders_per_user):
            orders.append((order_id, user_id, f"Product_{order_id}", round(order_id * 1.5, 2)))
            order_id += 1
    conn.executemany(
        "INSERT INTO orders (id, user_id, product, amount) VALUES (?, ?, ?, ?)", orders
    )
    conn.commit()


class TestNPlusOneDetection:
    """Tests that detect N+1 query patterns using set_trace_callback."""

    def test_n_plus_1_resolved_small(self, db_conn):
        """With 10 users, should use <= 3 queries (not 11)."""
        _setup_data(db_conn, num_users=10, orders_per_user=3)

        query_log = []
        db_conn.set_trace_callback(query_log.append)
        get_users_with_orders(db_conn)
        db_conn.set_trace_callback(None)

        assert len(query_log) <= 3, (
            f"N+1 detected! Expected <= 3 queries, got {len(query_log)}: {query_log}"
        )

    def test_n_plus_1_resolved_medium(self, db_conn):
        """With 50 users, should use <= 3 queries (not 51)."""
        _setup_data(db_conn, num_users=50, orders_per_user=2)

        query_log = []
        db_conn.set_trace_callback(query_log.append)
        get_users_with_orders(db_conn)
        db_conn.set_trace_callback(None)

        assert len(query_log) <= 3, (
            f"N+1 detected! Expected <= 3 queries, got {len(query_log)}"
        )

    def test_n_plus_1_resolved_large(self, db_conn):
        """With 100 users, should use <= 3 queries (not 101)."""
        _setup_data(db_conn, num_users=100, orders_per_user=5)

        query_log = []
        db_conn.set_trace_callback(query_log.append)
        get_users_with_orders(db_conn)
        db_conn.set_trace_callback(None)

        assert len(query_log) <= 3, (
            f"N+1 detected! Expected <= 3 queries, got {len(query_log)}"
        )

    def test_uses_join_or_in(self, db_conn):
        """Should use JOIN or IN clause, not per-user queries."""
        _setup_data(db_conn, num_users=5, orders_per_user=2)

        query_log = []
        db_conn.set_trace_callback(query_log.append)
        get_users_with_orders(db_conn)
        db_conn.set_trace_callback(None)

        all_sql = " ".join(query_log).upper()
        # Should use JOIN or IN to batch the orders query
        has_join = "JOIN" in all_sql
        has_in = "IN (" in all_sql or "IN(" in all_sql
        assert has_join or has_in, (
            f"Expected JOIN or IN clause for batch query. Queries: {query_log}"
        )


class TestCorrectness:
    """Tests to verify the optimized query returns correct results."""

    def test_all_users_returned(self, db_conn):
        """Should return all users."""
        _setup_data(db_conn, num_users=20, orders_per_user=3)
        result = get_users_with_orders(db_conn)
        assert len(result) == 20

    def test_orders_grouped_correctly(self, db_conn):
        """Each user should have exactly their orders."""
        _setup_data(db_conn, num_users=5, orders_per_user=4)
        result = get_users_with_orders(db_conn)

        for user_data in result:
            assert len(user_data["orders"]) == 4

    def test_user_without_orders(self, db_conn):
        """Users without orders should have empty orders list."""
        _setup_data(db_conn, num_users=10, orders_per_user=0)
        result = get_users_with_orders(db_conn)

        assert len(result) == 10
        for user_data in result:
            assert user_data["orders"] == []

    def test_mixed_orders(self, db_conn):
        """Some users with orders, some without."""
        conn = db_conn
        conn.executemany(
            "INSERT INTO users (id, username, email) VALUES (?, ?, ?)",
            [
                (1, "alice", "alice@example.com"),
                (2, "bob", "bob@example.com"),
                (3, "charlie", "charlie@example.com"),
            ],
        )
        conn.execute(
            "INSERT INTO orders (id, user_id, product, amount) VALUES (?, ?, ?, ?)",
            (1, 1, "Widget", 9.99),
        )
        conn.commit()

        result = get_users_with_orders(conn)
        assert len(result) == 3

        alice = next(u for u in result if u["user"]["username"] == "alice")
        assert len(alice["orders"]) == 1

        bob = next(u for u in result if u["user"]["username"] == "bob")
        assert bob["orders"] == []

    def test_order_data_integrity(self, db_conn):
        """Order data should match database exactly."""
        _setup_data(db_conn, num_users=3, orders_per_user=2)

        result = get_users_with_orders(db_conn)

        # Verify against direct DB query
        for user_data in result:
            user_id = user_data["user"]["id"]
            db_orders = db_conn.execute(
                "SELECT id, product, amount FROM orders WHERE user_id = ?",
                (user_id,),
            ).fetchall()

            assert len(user_data["orders"]) == len(db_orders)
            for order, db_order in zip(user_data["orders"], db_orders):
                assert order["id"] == db_order["id"]
                assert order["product"] == db_order["product"]
                assert order["amount"] == db_order["amount"]


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_database(self, db_conn):
        """Should handle empty database."""
        result = get_users_with_orders(db_conn)
        assert result == []

    def test_single_user_single_order(self, db_conn):
        """Minimal case: one user, one order."""
        db_conn.execute(
            "INSERT INTO users (id, username, email) VALUES (?, ?, ?)",
            (1, "alice", "alice@example.com"),
        )
        db_conn.execute(
            "INSERT INTO orders (id, user_id, product, amount) VALUES (?, ?, ?, ?)",
            (1, 1, "Widget", 9.99),
        )
        db_conn.commit()

        result = get_users_with_orders(db_conn)
        assert len(result) == 1
        assert len(result[0]["orders"]) == 1

    def test_many_orders_per_user(self, db_conn):
        """User with many orders."""
        _setup_data(db_conn, num_users=1, orders_per_user=50)

        result = get_users_with_orders(db_conn)
        assert len(result) == 1
        assert len(result[0]["orders"]) == 50
