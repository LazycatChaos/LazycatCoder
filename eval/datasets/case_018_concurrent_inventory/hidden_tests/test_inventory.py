import sqlite3
import threading
import pytest
from inventory import deduct_stock, get_stock


class ConnectionWrapper:
    """Wraps a sqlite3.Connection to intercept execute calls."""

    def __init__(self, conn, intercept_select=None):
        self._conn = conn
        self._intercept = intercept_select

    def execute(self, sql, params=()):
        if self._intercept and sql.strip().upper().startswith("SELECT"):
            result = self._conn.execute(sql, params)
            self._intercept()
            return result
        return self._conn.execute(sql, params)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


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


class TestConcurrentDeduction:
    """Tests for concurrent stock deduction using Barrier injection."""

    def test_concurrent_deduction_no_overdraw(self, tmp_path):
        """Concurrent deductions should not overdraw stock.

        Uses a Barrier to force both threads to read the same stock value
        simultaneously, guaranteeing a race condition in non-atomic code.
        """
        path = str(tmp_path / "test.db")
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE inventory (
                sku TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 1)")
        conn.commit()

        barrier = threading.Barrier(2)

        def intercept():
            barrier.wait()

        wrapped = ConnectionWrapper(conn, intercept_select=intercept)

        results = []
        errors = []

        def worker():
            try:
                result = deduct_stock(wrapped, 'SKU-001', 1)
                results.append(result)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        # Only one deduction should succeed
        assert sum(results) == 1, f"Expected exactly 1 success, got {sum(results)}"

        # Stock should be 0, not negative
        stock = get_stock(conn, 'SKU-001')
        assert stock == 0, f"Stock should be 0, got {stock}"

        conn.close()

    def test_concurrent_deduction_multiple_threads(self, tmp_path):
        """5 threads competing for 3 items should result in exactly 3 deductions.

        Each thread uses its own connection to the same database file.
        """
        path = str(tmp_path / "test.db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE inventory (
                sku TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 3)")
        conn.commit()
        conn.close()

        results = []
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                thread_conn = sqlite3.connect(path)
                result = deduct_stock(thread_conn, 'SKU-001', 1)
                thread_conn.close()
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        assert sum(results) == 3, f"Expected exactly 3 successes, got {sum(results)}"

        final_conn = sqlite3.connect(path)
        stock = get_stock(final_conn, 'SKU-001')
        assert stock == 0, f"Stock should be 0, got {stock}"
        final_conn.close()

    def test_concurrent_deduction_different_skus(self, tmp_path):
        """Concurrent deductions for different SKUs should not interfere."""
        path = str(tmp_path / "test.db")
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE inventory (
                sku TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-A', 1)")
        conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-B', 1)")
        conn.commit()

        results = []
        errors = []

        def worker_a():
            try:
                result = deduct_stock(conn, 'SKU-A', 1)
                results.append(('A', result))
            except Exception as e:
                errors.append(e)

        def worker_b():
            try:
                result = deduct_stock(conn, 'SKU-B', 1)
                results.append(('B', result))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker_a)
        t2 = threading.Thread(target=worker_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        # Both should succeed since they're different SKUs
        assert len(results) == 2
        assert all(r[1] for r in results)

        assert get_stock(conn, 'SKU-A') == 0
        assert get_stock(conn, 'SKU-B') == 0

        conn.close()


class TestAtomicUpdate:
    """Tests to verify atomic UPDATE is used."""

    def test_uses_atomic_update(self):
        """deduct_stock must use atomic UPDATE with WHERE guard."""
        import inspect
        source = inspect.getsource(deduct_stock)
        # Should use UPDATE with stock >= ? guard
        assert "stock >= ?" in source or "stock>=?" in source, (
            "deduct_stock must use atomic UPDATE with 'stock >= ?' guard"
        )
        # Should NOT do separate SELECT then UPDATE
        assert "SELECT" not in source.upper().split("UPDATE")[0], (
            "deduct_stock should not do SELECT before UPDATE (read-check-write race)"
        )

    def test_no_select_before_update(self):
        """Should not have a SELECT query before the UPDATE."""
        import inspect
        source = inspect.getsource(deduct_stock)
        lines = [l.strip() for l in source.split('\n') if l.strip() and not l.strip().startswith('#')]
        source_text = ' '.join(lines)

        # Check that SELECT doesn't appear before UPDATE
        select_pos = source_text.upper().find('SELECT')
        update_pos = source_text.upper().find('UPDATE')

        if select_pos != -1 and update_pos != -1:
            assert select_pos > update_pos, (
                "deduct_stock should not SELECT before UPDATE — use atomic UPDATE instead"
            )


class TestEdgeCases:
    """Edge case tests."""

    def test_deduct_zero(self, db_conn):
        """Deducting zero should succeed."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 0)")
        db_conn.commit()

        result = deduct_stock(db_conn, 'SKU-001', 0)
        assert result is True

    def test_deduct_negative_quantity(self, db_conn):
        """Negative quantity should be handled (either reject or add stock)."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 5)")
        db_conn.commit()

        # Should not crash
        result = deduct_stock(db_conn, 'SKU-001', -1)
        # Either True (adds stock) or False (rejects negative) is acceptable
        assert isinstance(result, bool)

    def test_large_quantity(self, db_conn):
        """Large quantity deduction should fail gracefully."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 1)")
        db_conn.commit()

        result = deduct_stock(db_conn, 'SKU-001', 1000000)
        assert result is False

        stock = get_stock(db_conn, 'SKU-001')
        assert stock == 1  # Unchanged

    def test_multiple_skus_independent(self, db_conn):
        """Different SKUs should have independent stock."""
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-A', 10)")
        db_conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-B', 5)")
        db_conn.commit()

        deduct_stock(db_conn, 'SKU-A', 3)
        deduct_stock(db_conn, 'SKU-B', 2)

        assert get_stock(db_conn, 'SKU-A') == 7
        assert get_stock(db_conn, 'SKU-B') == 3

    def test_commit_after_deduction(self, tmp_path):
        """Deduction should be committed (persist after connection close)."""
        path = str(tmp_path / "test.db")
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE inventory (
                sku TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("INSERT INTO inventory (sku, stock) VALUES ('SKU-001', 5)")
        conn.commit()

        deduct_stock(conn, 'SKU-001', 2)

        # Re-open connection to verify persistence
        conn.close()

        new_conn = sqlite3.connect(path)
        stock = get_stock(new_conn, 'SKU-001')
        assert stock == 3
        new_conn.close()
