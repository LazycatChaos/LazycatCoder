"""Payment processing module with idempotency support."""

import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import Enum


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


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the payments database with UNIQUE constraint on idempotency_key."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
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
    """Process a payment request with database-level idempotency.

    Uses a UNIQUE constraint on idempotency_key to prevent duplicate
    payments. If a duplicate is detected (IntegrityError), returns
    the existing payment result instead of creating a new one.
    """
    payment_id = str(uuid.uuid4())
    now = time.time()

    try:
        # Try to insert with UNIQUE constraint — will fail if key exists
        conn.execute(
            "INSERT INTO payments (id, idempotency_key, amount, currency, description, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payment_id, request.idempotency_key, request.amount, request.currency,
             request.description, PaymentStatus.PENDING.value, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Idempotency key already exists — return existing result
        row = conn.execute(
            "SELECT id, status FROM payments WHERE idempotency_key = ?",
            (request.idempotency_key,),
        ).fetchone()

        if row is None:
            # Should not happen, but handle gracefully
            return PaymentResult(
                status_code=500,
                body={"message": "Internal error"},
            )

        existing_id, existing_status = row

        if existing_status == PaymentStatus.SUCCESS.value:
            return PaymentResult(
                status_code=200,
                body={
                    "payment_id": existing_id,
                    "status": PaymentStatus.SUCCESS.value,
                    "message": "Payment already processed",
                },
            )
        elif existing_status == PaymentStatus.PENDING.value:
            # Still processing — tell client to retry later
            return PaymentResult(
                status_code=202,
                body={
                    "payment_id": existing_id,
                    "status": PaymentStatus.PENDING.value,
                    "message": "Payment still processing, please retry later",
                },
            )
        else:
            # Failed — allow retry by updating the existing record
            conn.execute(
                "UPDATE payments SET status = ?, created_at = ? WHERE id = ?",
                (PaymentStatus.PENDING.value, time.time(), existing_id),
            )
            conn.commit()
            # Fall through to process the payment below
            payment_id = existing_id
            now = time.time()

    # Process the payment
    try:
        # ... call external payment gateway ...
        conn.execute(
            "UPDATE payments SET status = ? WHERE id = ?",
            (PaymentStatus.SUCCESS.value, payment_id),
        )
        conn.commit()

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
