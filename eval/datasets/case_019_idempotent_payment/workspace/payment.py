"""Payment processing module with idempotency support."""

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class PaymentResult:
    status_code: int
    body: dict


@dataclass
class PaymentRequest:
    idempotency_key: str
    amount: float
    currency: str
    description: str


# In-memory idempotency cache (LOST ON RESTART!)
_idempotency_cache: dict = {}


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the payments database."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def process_payment(conn: sqlite3.Connection, request: PaymentRequest) -> PaymentResult:
    """Process a payment request with idempotency.

    BUG: Uses in-memory dict for idempotency check.
    If the service restarts, the cache is lost and duplicate
    payments will be processed.
    """
    # Check in-memory cache (fragile!)
    if request.idempotency_key in _idempotency_cache:
        cached = _idempotency_cache[request.idempotency_key]
        return PaymentResult(
            status_code=200,
            body={
                "payment_id": cached["id"],
                "status": cached["status"],
                "message": "Payment already processed",
            },
        )

    payment_id = str(uuid.uuid4())
    now = time.time()

    # Insert payment record
    conn.execute(
        "INSERT INTO payments (id, idempotency_key, amount, currency, description, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (payment_id, request.idempotency_key, request.amount, request.currency,
         request.description, PaymentStatus.PENDING.value, now),
    )
    conn.commit()

    # Simulate payment processing
    try:
        # ... call external payment gateway ...
        conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (PaymentStatus.SUCCESS.value, payment_id),
        )
        conn.commit()

        # Update in-memory cache
        _idempotency_cache[request.idempotency_key] = {
            "id": payment_id,
            "status": PaymentStatus.SUCCESS.value,
        }

        return PaymentResult(
            status_code=200,
            body={
                "payment_id": payment_id,
                "status": PaymentStatus.SUCCESS.value,
                "message": "Payment successful",
            },
        )
    except Exception:
        conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (PaymentStatus.FAILED.value, payment_id),
        )
        conn.commit()
        return PaymentResult(
            status_code=500,
            body={
                "payment_id": payment_id,
                "status": PaymentStatus.FAILED.value,
                "message": "Payment processing failed",
            },
        )
