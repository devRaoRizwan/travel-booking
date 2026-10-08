import sqlite3

from app.config import Settings
from app.infrastructure.database import write_tx
from app.infrastructure.psp_client import PspClient, PspRejected, PspUnavailable
from app.repositories import bookings, refunds


def create_refund(conn: sqlite3.Connection, booking: sqlite3.Row, payment_attempt: sqlite3.Row, amount_pkr: int,
                  reason: str, breakdown: dict) -> None:
    if refunds.create_once(conn, booking["id"], payment_attempt["id"], amount_pkr, reason, breakdown):
        bookings.log_event(conn, booking["id"], booking["state"], booking["state"], "refund_created",
                           f"{reason}: PKR {amount_pkr}")


def claim_due(conn: sqlite3.Connection, settings: Settings, limit: int = 10) -> list[sqlite3.Row]:
    with write_tx(conn):
        return refunds.claim_due(conn, int(settings.refund_retry_s * 1000), limit)


# Refund id is the PSP idempotency key; on failure the refund stays pending and is retried.
def execute(conn: sqlite3.Connection, psp: PspClient, refund: sqlite3.Row) -> None:
    if refund["amount_pkr"] == 0:
        psp_refund_id = None
    else:
        try:
            psp_response = psp.refund(refund["id"], refund["psp_payment_id"], refund["amount_pkr"])
        except (PspUnavailable, PspRejected):
            return
        psp_refund_id = psp_response["refund_id"]
    with write_tx(conn):
        if refunds.mark_succeeded(conn, refund["id"], psp_refund_id):
            booking = bookings.get(conn, refund["booking_id"])
            bookings.log_event(conn, booking["id"], booking["state"], booking["state"], "refund_succeeded",
                               psp_refund_id)
