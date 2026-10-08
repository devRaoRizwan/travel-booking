import json
import sqlite3

from app.config import Settings
from app.domain import states
from app.domain.refunds import full_refund
from app.infrastructure.airline_client import IssueResult
from app.infrastructure.clock import now_ms
from app.infrastructure.database import write_tx
from app.repositories import bookings, payments
from app.services.refunds import create_refund

LEASE_MARGIN_S = 5


# An expired lease (worker crash) makes the booking claimable again.
def claim_due(conn: sqlite3.Connection, settings: Settings, limit: int = 10) -> list[int]:
    lease_ms = int((settings.airline_timeout_s + LEASE_MARGIN_S) * 1000)
    with write_tx(conn):
        return bookings.claim_due_ticketing(conn, states.TICKETING, lease_ms, limit)


def issue_request(conn: sqlite3.Connection, booking_id: int) -> tuple[str, str, list]:
    booking = bookings.get(conn, booking_id)
    return booking["ref"], booking["fare_id"], json.loads(booking["passengers_json"])


# Timeouts after max attempts go to review, not refund: a ticket may already exist.
def record_result(conn: sqlite3.Connection, settings: Settings, booking_id: int, result: IssueResult) -> None:
    with write_tx(conn):
        booking = bookings.get(conn, booking_id)
        if booking["state"] != states.TICKETING:
            return
        if result.outcome == "issued":
            bookings.transition(conn, booking_id, (states.TICKETING,), states.TICKETED, "ticket_issued", result.pnr,
                                pnr=result.pnr, ticketing_lease_until=None, last_error=None)
        elif result.outcome == "rejected":
            bookings.transition(conn, booking_id, (states.TICKETING,), states.TICKETING_FAILED, "airline_rejected",
                                result.error, ticketing_lease_until=None, last_error=result.error)
            payment_attempt = payments.captured_for_booking(conn, booking_id)
            create_refund(conn, bookings.get(conn, booking_id), payment_attempt, payment_attempt["amount_pkr"],
                          "ticketing_rejected", full_refund(result.error))
        elif booking["ticketing_attempts"] >= settings.ticketing_max_attempts:
            bookings.transition(conn, booking_id, (states.TICKETING,), states.NEEDS_REVIEW,
                                "ticketing_retries_exhausted", result.error,
                                ticketing_lease_until=None, last_error=result.error)
        else:
            backoff_s = settings.ticketing_backoff_s * 2 ** (booking["ticketing_attempts"] - 1)
            bookings.update_fields(conn, booking_id, ticketing_lease_until=None, last_error=result.error,
                                   ticketing_next_at=now_ms() + int(backoff_s * 1000))
            bookings.log_event(
                conn, booking_id, states.TICKETING, states.TICKETING, "ticketing_retry_scheduled",
                f"attempt {booking['ticketing_attempts']} failed: {result.error}; next in {backoff_s:.1f}s")
