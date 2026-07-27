"""Webhook signature verification module."""

import hashlib
import hmac
import time


def compute_signature(body: bytes, secret: str, timestamp: int | None = None) -> str:
    """Compute HMAC-SHA256 signature for the given body.

    If timestamp is provided, it is included in the signed payload
    to prevent replay attacks.
    """
    if timestamp is not None:
        payload = f"{timestamp}.".encode("utf-8") + body
    else:
        payload = body
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def verify_webhook(
    body: bytes,
    signature_header: str,
    secret: str,
    tolerance: int = 300,
) -> bool:
    """Verify a webhook signature with timestamp-based replay protection.

    Args:
        body: Raw request body bytes.
        signature_header: Header in format "t=<timestamp>,v1=<signature>".
        secret: The shared secret key.
        tolerance: Maximum allowed clock skew in seconds (default 300).

    Returns:
        True if the signature is valid and timestamp is within tolerance.
    """
    if not signature_header or not isinstance(signature_header, str):
        return False

    # Parse header: "t=1705312200,v1=abc123..."
    parts = {}
    for segment in signature_header.split(","):
        if "=" in segment:
            key, _, value = segment.partition("=")
            parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        return False

    try:
        timestamp = int(parts["t"])
    except (ValueError, TypeError):
        return False

    provided_sig = parts["v1"]

    # Check timestamp tolerance
    now = int(time.time())
    if abs(now - timestamp) > tolerance:
        return False

    # Compute expected signature (includes timestamp in payload)
    expected = compute_signature(body, secret, timestamp)

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected, provided_sig)
