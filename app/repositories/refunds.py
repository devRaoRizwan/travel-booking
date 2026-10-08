import json
import secrets
import sqlite3

from app.infrastructure.clock import now_ms


# UNIQUE(payment_attempt_id): at most one refund per captured payment.
def create_once(conn: sqlite3.Connection, booking_id: int, payment_attempt_id: str, amount_pkr: int,
                reason: str, breakdown: dict) -> bool:
    now = now_ms()
    cursor = conn.execute(
        """INSERT OR IGNORE INTO refunds (id, booking_id, payment_attempt_id, amount_pkr, reason,
               breakdown_json, status, next_at, created_at) VALUES (?,?,?,?,?,?, 'pending', ?, ?)""",
        ("rf_" + secrets.token_hex(8), booking_id, payment_attempt_id, amount_pkr, reason,
         json.dumps(breakdown), now, now),
    )
    return cursor.rowcount == 1


def latest_for_booking(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM refunds WHERE booking_id = ? ORDER BY created_at DESC LIMIT 1",
                        (booking_id,)).fetchone()


# Advancing next_at acts as both the lease and the retry delay.
def claim_due(conn: sqlite3.Connection, retry_ms: int, limit: int) -> list[sqlite3.Row]:
    now = now_ms()
    due_refunds = conn.execute(
        """SELECT refund.*, attempt.psp_payment_id
           FROM refunds refund JOIN payment_attempts attempt ON attempt.id = refund.payment_attempt_id
           WHERE refund.status = 'pending' AND refund.next_at <= ? LIMIT ?""", (now, limit)).fetchall()
    for refund in due_refunds:
        conn.execute("UPDATE refunds SET next_at = ?, attempts = attempts + 1 WHERE id = ?",
                     (now + retry_ms, refund["id"]))
    return due_refunds


def mark_succeeded(conn: sqlite3.Connection, refund_id: str, psp_refund_id: str | None) -> bool:
    cursor = conn.execute(
        "UPDATE refunds SET status = 'succeeded', psp_refund_id = ? WHERE id = ? AND status = 'pending'",
        (psp_refund_id, refund_id),
    )
    return cursor.rowcount == 1
