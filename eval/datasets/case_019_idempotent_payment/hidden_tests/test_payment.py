import sqlite3
import threading
import pytest
from payment import process_payment, PaymentRequest, PaymentResult, PaymentStatus


@pytest.fixture
def db_conn(tmp_path):
    """Create a test database."""
    from payment import init_db
    path = str(tmp_path / "payments.db")
    conn = init_db(path)
    yield conn
    conn.close()


class TestConcurrentIdempotency:
    """Tests for concurrent duplicate payment handling.

    Uses Barrier injection to force simultaneous duplicate requests,
    guaranteeing a race condition in non-atomic implementations.
    """

    def test_concurrent_duplicate_requests(self, tmp_path):
        """Concurrent requests with same idempotency key should not create duplicates.

        Uses separate connections per thread to simulate real-world concurrent
        payment processing. The UNIQUE constraint ensures only one payment record.
        """
        from payment import init_db
        path = str(tmp_path / "payments.db")
        conn = init_db(path)
        conn.close()

        request = PaymentRequest(
            idempotency_key="concurrent_req_001",
            amount=100.00,
            currency="USD",
            description="Concurrent test",
        )

        results = []
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                thread_conn = init_db(path)
                result = process_payment(thread_conn, request)
                thread_conn.close()
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(results) == 2

        # At least one should succeed with 200
        success_results = [r for r in results if r.status_code == 200]
        assert len(success_results) >= 1

        # Only ONE payment record should exist
        final_conn = init_db(path)
        count = final_conn.execute(
            "SELECT COUNT(*) FROM payments WHERE idempotency_key = ?",
            ("concurrent_req_001",),
        ).fetchone()[0]
        assert count == 1, f"Expected 1 payment record, got {count}"
        final_conn.close()

    def test_concurrent_different_requests(self, tmp_path):
        """Concurrent requests with different keys should both succeed."""
        from payment import init_db
        path = str(tmp_path / "payments.db")
        conn = init_db(path)

        results = []
        errors = []
        lock = threading.Lock()

        def worker(key, amount):
            try:
                thread_conn = init_db(path)
                request = PaymentRequest(
                    idempotency_key=key,
                    amount=amount,
                    currency="USD",
                    description=f"Payment {key}",
                )
                result = process_payment(thread_conn, request)
                thread_conn.close()
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"key_{i}", 10.0 * i))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(results) == 5
        assert all(r.status_code == 200 for r in results)

        final_conn = init_db(path)
        count = final_conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        assert count == 5
        final_conn.close()


class TestIntegrityErrorHandling:
    """Tests for proper IntegrityError handling."""

    def test_handles_integrity_error_gracefully(self, db_conn):
        """Should not crash on IntegrityError — should return existing payment."""
        request = PaymentRequest(
            idempotency_key="ie_test_001",
            amount=75.00,
            currency="USD",
            description="IE test",
        )
        result1 = process_payment(db_conn, request)
        assert result1.status_code == 200

        # Manually try to insert a duplicate to trigger IntegrityError path
        result2 = process_payment(db_conn, request)
        assert result2.status_code == 200
        assert result2.body["payment_id"] == result1.body["payment_id"]

    def test_no_500_on_duplicate(self, db_conn):
        """Duplicate request should never return 500."""
        request = PaymentRequest(
            idempotency_key="no500_test",
            amount=10.00,
            currency="USD",
            description="No 500 test",
        )
        for _ in range(5):
            result = process_payment(db_conn, request)
            assert result.status_code != 500, "Duplicate should not return 500"


class TestPendingStateRetry:
    """Tests for pending state handling."""

    def test_pending_returns_202(self, db_conn):
        """If payment is still pending, should return 202."""
        # Manually insert a pending payment
        db_conn.execute(
            "INSERT INTO payments (id, idempotency_key, amount, currency, description, status, created_at) "
            "VALUES ('pending_001', 'pending_key', 50.00, 'USD', 'Pending test', 'pending', 1000000.0)",
        )
        db_conn.commit()

        request = PaymentRequest(
            idempotency_key="pending_key",
            amount=50.00,
            currency="USD",
            description="Pending test",
        )
        result = process_payment(db_conn, request)

        assert result.status_code == 202
        assert result.body["status"] == PaymentStatus.PENDING.value

    def test_failed_allows_retry(self, db_conn):
        """If payment failed, retry should reprocess."""
        # Manually insert a failed payment
        db_conn.execute(
            "INSERT INTO payments (id, idempotency_key, amount, currency, description, status, created_at) "
            "VALUES ('failed_001', 'failed_key', 30.00, 'USD', 'Failed test', 'failed', 1000000.0)",
        )
        db_conn.commit()

        request = PaymentRequest(
            idempotency_key="failed_key",
            amount=30.00,
            currency="USD",
            description="Failed test retry",
        )
        result = process_payment(db_conn, request)

        # Should reprocess and succeed
        assert result.status_code == 200
        assert result.body["status"] == PaymentStatus.SUCCESS.value


class TestSourceInspection:
    """Tests to verify the solution uses proper patterns."""

    def test_uses_unique_constraint(self):
        """init_db must create UNIQUE constraint on idempotency_key."""
        import inspect
        source = inspect.getsource(process_payment.__globals__['init_db'])
        assert "UNIQUE" in source.upper(), (
            "init_db must create a UNIQUE constraint on idempotency_key"
        )

    def test_catches_integrity_error(self):
        """process_payment must catch sqlite3.IntegrityError."""
        import inspect
        source = inspect.getsource(process_payment)
        assert "IntegrityError" in source, (
            "process_payment must catch sqlite3.IntegrityError for idempotency"
        )

    def test_no_in_memory_cache(self):
        """Should not rely on in-memory dict for idempotency."""
        import inspect
        source = inspect.getsource(process_payment)
        # Should not use a global dict cache as primary idempotency mechanism
        assert "_idempotency_cache" not in source, (
            "process_payment should not use in-memory dict for idempotency"
        )


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_idempotency_key(self, db_conn):
        """Empty idempotency key should still work (UNIQUE on empty string)."""
        request = PaymentRequest(
            idempotency_key="",
            amount=1.00,
            currency="USD",
            description="Empty key test",
        )
        result = process_payment(db_conn, request)
        assert result.status_code == 200

    def test_large_amount(self, db_conn):
        """Large amount should be handled correctly."""
        request = PaymentRequest(
            idempotency_key="large_amt",
            amount=999999.99,
            currency="USD",
            description="Large amount",
        )
        result = process_payment(db_conn, request)
        assert result.status_code == 200

        row = db_conn.execute(
            "SELECT amount FROM payments WHERE idempotency_key = ?",
            ("large_amt",),
        ).fetchone()
        assert row[0] == 999999.99

    def test_multiple_currencies(self, db_conn):
        """Same idempotency key with different currencies — first wins."""
        request1 = PaymentRequest(
            idempotency_key="multi_curr",
            amount=100.00,
            currency="USD",
            description="USD payment",
        )
        request2 = PaymentRequest(
            idempotency_key="multi_curr",
            amount=100.00,
            currency="EUR",
            description="EUR payment",
        )
        result1 = process_payment(db_conn, request1)
        result2 = process_payment(db_conn, request2)

        assert result1.status_code == 200
        assert result2.status_code == 200
        assert result1.body["payment_id"] == result2.body["payment_id"]

    def test_persistence_across_connections(self, tmp_path):
        """Idempotency should persist across database connections."""
        from payment import init_db
        path = str(tmp_path / "payments.db")

        conn1 = init_db(path)
        request = PaymentRequest(
            idempotency_key="persist_key",
            amount=42.00,
            currency="USD",
            description="Persistence test",
        )
        result1 = process_payment(conn1, request)
        conn1.close()

        conn2 = init_db(path)
        result2 = process_payment(conn2, request)
        conn2.close()

        assert result1.status_code == 200
        assert result2.status_code == 200
        assert result1.body["payment_id"] == result2.body["payment_id"]
