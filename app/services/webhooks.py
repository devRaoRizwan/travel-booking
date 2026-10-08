import sqlite3

from app.domain import states
from app.domain.errors import NotFound
from app.domain.refunds import full_refund
from app.infrastructure.clock import now_ms
from app.infrastructure.database import write_tx
from app.repositories import bookings, payments, webhook_events
from app.services.expiry import expire_due
from app.services.payments import fail_attempt
from app.services.refunds import create_refund

UNPAID_STATES = (states.PAYMENT_PENDING, states.HELD, states.EXPIRED, states.CANCELLED)


# Idempotent per event id; the first terminal outcome per attempt wins.
def apply_psp_event(conn: sqlite3.Connection, event: dict) -> str:
    event_data = event["data"]
    with write_tx(conn):
        if not webhook_events.record_once(conn, event):
            return "duplicate_event"

        payment_attempt = payments.get(conn, event_data["merchant_reference"])
        if payment_attempt is None:
            # Rollback discards the event row so a PSP redelivery is processed.
            raise NotFound("UNKNOWN_PAYMENT", "no payment attempt for merchant_reference")
        payments.set_psp_payment_id(conn, payment_attempt["id"], event_data["payment_id"])
        if payment_attempt["status"] != "pending":
            return f"ignored_attempt_already_{payment_attempt['status']}"

        if event["type"] == "payment.failed":
            fail_attempt(conn, payment_attempt["id"], "psp_declined")
            return "payment_failed"
        if event["type"] != "payment.succeeded":
            return "ignored_event_type"
        return _apply_payment_succeeded(conn, payment_attempt, event_data)


def _apply_payment_succeeded(conn: sqlite3.Connection, payment_attempt: sqlite3.Row, event_data: dict) -> str:
    payments.set_status(conn, payment_attempt["id"], "succeeded")
    booking_id = payment_attempt["booking_id"]

    if event_data.get("amount_pkr") != payment_attempt["amount_pkr"] or event_data.get("currency") != "PKR":
        bookings.transition(
            conn, booking_id, UNPAID_STATES, states.NEEDS_REVIEW, "payment_amount_mismatch",
            f"expected {payment_attempt['amount_pkr']} PKR, "
            f"got {event_data.get('amount_pkr')} {event_data.get('currency')}")
        return "amount_mismatch"

    expire_due(conn, booking_id)
    if bookings.transition(conn, booking_id, (states.PAYMENT_PENDING,), states.TICKETING, "payment_succeeded",
                           payment_attempt["id"], ticketing_next_at=now_ms()):
        return "ticketing"

    # Booking is EXPIRED or CANCELLED: seat no longer held, so refund the captured amount in full.
    booking = bookings.get(conn, booking_id)
    create_refund(conn, booking, payment_attempt, payment_attempt["amount_pkr"],
                  f"payment_after_{booking['state']}",
                  full_refund(f"payment captured after booking became {booking['state']}"))
    return f"refund_payment_after_{booking['state']}"
