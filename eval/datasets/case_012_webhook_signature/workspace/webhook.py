"""Webhook signature verification module."""

import hashlib
import hmac
import time


def compute_signature(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for the given body."""
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    """Verify a webhook signature.

    Args:
        body: Raw request body bytes.
        signature: The signature string to verify.
        secret: The shared secret key.

    Returns:
        True if the signature is valid, False otherwise.
    """
    expected = compute_signature(body, secret)
    return signature == expected
