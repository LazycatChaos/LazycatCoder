import sqlite3
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


class TestBasicPayment:
    """Basic payment processing tests."""

    def test_successful_payment(self, db_conn):
        """First payment should succeed."""
        request = PaymentRequest(
            idempotency_key="req_001",
            amount=99.99,
            currency="USD",
            description="Test purchase",
        )
        result = process_payment(db_conn, request)

        assert result.status_code == 200
        assert result.body["status"] == PaymentStatus.SUCCESS.value
        assert "payment_id" in result.body

    def test_payment_recorded_in_db(self, db_conn):
        """Payment should be stored in database."""
        request = PaymentRequest(
            idempotency_key="req_002",
            amount=50.00,
            currency="EUR",
            description="Order #123",
        )
        process_payment(db_conn, request)

        row = db_conn.execute(
            "SELECT amount, currency, status FROM payments WHERE idempotency_key = ?",
            ("req_002",),
        ).fetchone()
        assert row is not None
        assert row[0] == 50.00
        assert row[1] == "EUR"
        assert row[2] == PaymentStatus.SUCCESS.value


class TestIdempotency:
    """Basic idempotency tests."""

    def test_duplicate_request_returns_same_result(self, db_conn):
        """Same idempotency key should return existing result."""
        request = PaymentRequest(
            idempotency_key="req_003",
            amount=25.00,
            currency="USD",
            description="Duplicate test",
        )
        result1 = process_payment(db_conn, request)
        result2 = process_payment(db_conn, request)

        assert result1.status_code == 200
        assert result2.status_code == 200
        assert result1.body["payment_id"] == result2.body["payment_id"]
        assert "already processed" in result2.body["message"].lower()

    def test_different_keys_create_different_payments(self, db_conn):
        """Different idempotency keys should create separate payments."""
        req1 = PaymentRequest(
            idempotency_key="req_a",
            amount=10.00,
            currency="USD",
            description="First",
        )
        req2 = PaymentRequest(
            idempotency_key="req_b",
            amount=20.00,
            currency="USD",
            description="Second",
        )
        result1 = process_payment(db_conn, req1)
        result2 = process_payment(db_conn, req2)

        assert result1.body["payment_id"] != result2.body["payment_id"]
