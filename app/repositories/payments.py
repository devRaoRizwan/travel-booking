import sqlite3

from app.infrastructure.clock import now_ms


def get(conn: sqlite3.Connection, attempt_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM payment_attempts WHERE id = ?", (attempt_id,)).fetchone()


# Unique index rejects a second pending attempt for the same booking.
def insert_pending(conn: sqlite3.Connection, attempt_id: str, booking_id: int, amount_pkr: int, method: str) -> None:
    now = now_ms()
    conn.execute(
        """INSERT INTO payment_attempts (id, booking_id, amount_pkr, method, status, created_at, updated_at)
           VALUES (?,?,?,?, 'pending', ?, ?)""",
        (attempt_id, booking_id, amount_pkr, method, now, now),
    )


def set_status(conn: sqlite3.Connection, attempt_id: str, status: str) -> None:
    conn.execute("UPDATE payment_attempts SET status = ?, updated_at = ? WHERE id = ?", (status, now_ms(), attempt_id))


def set_psp_payment_id(conn: sqlite3.Connection, attempt_id: str, psp_payment_id: str) -> None:
    conn.execute("UPDATE payment_attempts SET psp_payment_id = COALESCE(psp_payment_id, ?) WHERE id = ?",
                 (psp_payment_id, attempt_id))


def latest_for_booking(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM payment_attempts WHERE booking_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (booking_id,),
    ).fetchone()


def captured_for_booking(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM payment_attempts WHERE booking_id = ? AND status = 'succeeded'", (booking_id,)
    ).fetchone()
