import time
from unittest.mock import patch

import pytest
from webhook import verify_webhook, compute_signature


def _make_header(timestamp: int, body: bytes, secret: str) -> str:
    """Create a valid signature header."""
    sig = compute_signature(body, secret, timestamp)
    return f"t={timestamp},v1={sig}"


class TestTimestampTolerance:
    """Tests for timestamp-based replay protection."""

    def test_expired_timestamp(self):
        """Timestamp older than tolerance should return False."""
        body = b'{"event": "test"}'
        secret = "secret"
        # 10 minutes ago (beyond default 300s tolerance)
        old_ts = int(time.time()) - 601
        header = _make_header(old_ts, body, secret)

        assert verify_webhook(body, header, secret) is False

    def test_future_timestamp_beyond_tolerance(self):
        """Timestamp far in the future should return False."""
        body = b'{"event": "test"}'
        secret = "secret"
        # 10 minutes in the future
        future_ts = int(time.time()) + 601
        header = _make_header(future_ts, body, secret)

        assert verify_webhook(body, header, secret) is False

    def test_timestamp_at_tolerance_boundary(self):
        """Timestamp exactly at tolerance should still be valid."""
        body = b'{"event": "test"}'
        secret = "secret"
        # Exactly at tolerance (300s)
        ts = int(time.time()) - 300
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret, tolerance=300) is True

    def test_custom_tolerance(self):
        """Custom tolerance should be respected."""
        body = b'{"event": "test"}'
        secret = "secret"
        # 60 seconds ago
        ts = int(time.time()) - 60
        header = _make_header(ts, body, secret)

        # Should pass with 120s tolerance
        assert verify_webhook(body, header, secret, tolerance=120) is True
        # Should fail with 30s tolerance
        assert verify_webhook(body, header, secret, tolerance=30) is False


class TestConstantTimeCompare:
    """Tests to ensure constant-time comparison is used."""

    def test_constant_time_compare_used(self):
        """verify_webhook must use hmac.compare_digest, not ==."""
        import inspect
        source = inspect.getsource(verify_webhook)
        assert "compare_digest" in source, (
            "verify_webhook must use hmac.compare_digest for constant-time comparison"
        )

    def test_no_direct_equality_on_signatures(self):
        """Should not use == or != for signature comparison."""
        import inspect
        source = inspect.getsource(verify_webhook)
        # Check that the function doesn't compare signatures with ==
        # (it should use compare_digest instead)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if "expected" in stripped and "provided_sig" in stripped:
                assert "==" not in stripped and "!=" not in stripped, (
                    "Signature comparison must use hmac.compare_digest, not =="
                )


class TestReplayProtection:
    """Tests for replay attack prevention."""

    def test_same_signature_reused_with_new_timestamp_fails(self):
        """A signature from an old request should not be reusable."""
        body = b'{"event": "payment.completed", "amount": 10000}'
        secret = "payment-secret"
        old_ts = int(time.time()) - 600
        header = _make_header(old_ts, body, secret)

        # Old signature should be rejected
        assert verify_webhook(body, header, secret) is False

    def test_fresh_signature_works(self):
        """A fresh signature should always work."""
        body = b'{"event": "payment.completed", "amount": 10000}'
        secret = "payment-secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_body(self):
        """Empty body should still be verifiable."""
        body = b""
        secret = "secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True

    def test_binary_body(self):
        """Binary body should work."""
        body = bytes(range(256))
        secret = "secret"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True

    def test_unicode_secret(self):
        """Unicode secret should work."""
        body = b'{"event": "test"}'
        secret = "密钥🔑"
        ts = int(time.time())
        header = _make_header(ts, body, secret)

        assert verify_webhook(body, header, secret) is True

    def test_header_with_extra_spaces(self):
        """Header with spaces around = should still parse."""
        body = b'{"event": "test"}'
        secret = "secret"
        ts = int(time.time())
        sig = compute_signature(body, secret, ts)
        header = f"t = {ts}, v1 = {sig}"

        assert verify_webhook(body, header, secret) is True
