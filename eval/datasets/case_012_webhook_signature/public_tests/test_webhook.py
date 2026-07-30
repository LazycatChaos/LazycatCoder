import time
from unittest.mock import patch

import pytest
from webhook import verify_webhook, compute_signature


def _make_header(timestamp: int, body: bytes, secret: str) -> str:
    """Create a valid signature header."""
    sig = compute_signature(body, secret, timestamp)
    return f"t={timestamp},v1={sig}"


class TestWebhookValid:
    """Tests for valid webhook signatures."""

    def test_valid_signature(self):
        """A correctly signed request should verify."""
        body = b'{"event": "user.created", "id": 123}'
        secret = "my-secret-key"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True

    def test_valid_signature_with_different_body(self):
        """Different body content should still work with correct signature."""
        body = b'{"type": "payment.completed", "amount": 9999}'
        secret = "another-secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True


class TestWebhookInvalid:
    """Tests for invalid webhook signatures."""

    def test_invalid_signature(self):
        """Wrong signature should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "t=1234567890,v1=invalidsig", "secret") is False

    def test_tampered_body(self):
        """Tampered body should fail verification."""
        body = b'{"event": "user.created"}'
        secret = "secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        # Tamper with body
        tampered = b'{"event": "user.deleted"}'
        assert verify_webhook(tampered, header, secret) is False

    def test_wrong_secret(self):
        """Wrong secret should fail verification."""
        body = b'{"event": "test"}'
        secret = "correct-secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, "wrong-secret") is False


class TestWebhookMissingMalformed:
    """Tests for missing or malformed headers."""

    def test_missing_header(self):
        """Missing signature header should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "", "secret") is False

    def test_none_header(self):
        """None header should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, None, "secret") is False  # type: ignore

    def test_malformed_header_no_timestamp(self):
        """Header without timestamp should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "v1=abc123", "secret") is False

    def test_malformed_header_no_signature(self):
        """Header without signature should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "t=1234567890", "secret") is False

    def test_malformed_header_garbage(self):
        """Completely malformed header should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "garbage", "secret") is False

    def test_malformed_header_bad_timestamp(self):
        """Non-integer timestamp should return False."""
        body = b'{"event": "test"}'
        assert verify_webhook(body, "t=abc,v1=sig", "secret") is False
