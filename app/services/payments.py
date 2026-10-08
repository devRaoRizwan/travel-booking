import secrets
import sqlite3

from app.config import Settings
from app.domain import states
from app.domain.errors import Conflict, UpstreamError
from app.domain.principal import Principal
from app.infrastructure.database import write_tx
from app.infrastructure.psp_client import PspClient, PspRejected, PspUnavailable
from app.repositories import bookings, payments
from app.repositories import idempotency as idempotency_repo
from app.services import idempotency
from app.services.auth import load_visible_booking
from app.services.commands import PaymentRequest
from app.services.expiry import expire_due


# The attempt is committed before the PSP call because the webhook can arrive immediately.
def start_payment(conn: sqlite3.Connection, settings: Settings, psp: PspClient, principal: Principal, ref: str,
                  payment_request: PaymentRequest, idempotency_key: str) -> idempotency.Outcome:
    principal.require("payments:write")
    scope = principal.idempotency_scope
    body_hash = idempotency.request_hash("POST", f"/v1/bookings/{ref}/payments",
                                         payment_request.model_dump(mode="json"))

    with write_tx(conn):
        booking = load_visible_booking(conn, principal, ref)
        record = idempotency.lookup(conn, scope, idempotency_key, body_hash)
        if stored_outcome := idempotency.replay(record):
            return stored_outcome
        if record is not None:
            # Earlier request with this key has no final response (PSP unavailable): resume its attempt.
            payment_attempt = payments.get(conn, record["resource_id"])
        else:
            payment_attempt = _open_attempt(conn, booking, payment_request.method)
            idempotency_repo.insert(conn, scope, idempotency_key, body_hash, resource_id=payment_attempt["id"])

    try:
        psp_response = psp.create_payment(payment_attempt["id"], payment_attempt["amount_pkr"],
                                          payment_attempt["method"], settings.public_base_url + "/v1/webhooks/psp")
    except PspUnavailable as error:
        # Attempt stays pending; same-key retry resubmits the same attempt id (PSP-side dedup).
        raise UpstreamError("PSP_UNAVAILABLE",
                            f"payment provider unavailable ({error}); retry with the same Idempotency-Key",
                            retry_after=2)
    except PspRejected as error:
        rejection_body = {"error": {"code": "PSP_REJECTED", "message": str(error)}}
        with write_tx(conn):
            fail_attempt(conn, payment_attempt["id"], f"psp_rejected: {error}")
            idempotency_repo.store_response(conn, scope, idempotency_key, 502, rejection_body)
        return idempotency.Outcome(502, rejection_body)

    with write_tx(conn):
        payments.set_psp_payment_id(conn, payment_attempt["id"], psp_response["payment_id"])
        idempotency_repo.store_response(conn, scope, idempotency_key, 202, {
            "payment_id": payment_attempt["id"],
            "booking_ref": ref,
            "status": "pending",
            "amount_pkr": payment_attempt["amount_pkr"],
            "currency": "PKR",
            "method": payment_attempt["method"],
            "psp_payment_id": psp_response["payment_id"],
        })
        # Re-read: a concurrent request with the same key may have stored first.
        stored_outcome = idempotency.replay(idempotency_repo.get(conn, scope, idempotency_key))
    return idempotency.Outcome(stored_outcome.status, stored_outcome.body)


def _open_attempt(conn: sqlite3.Connection, booking: sqlite3.Row, method: str) -> sqlite3.Row:
    expire_due(conn, booking["id"])
    current_state = bookings.get(conn, booking["id"])["state"]
    if current_state == states.EXPIRED:
        raise Conflict("HOLD_EXPIRED", "the hold has expired; create a new hold")
    if current_state == states.PAYMENT_PENDING:
        raise Conflict("PAYMENT_IN_PROGRESS", "a payment for this booking is already in progress")
    if current_state != states.HELD:
        raise Conflict("INVALID_STATE", f"cannot pay a booking in state {current_state!r}")
    attempt_id = "pa_" + secrets.token_hex(8)
    payments.insert_pending(conn, attempt_id, booking["id"], booking["total_pkr"], method)
    bookings.transition(conn, booking["id"], (states.HELD,), states.PAYMENT_PENDING, "payment_started", attempt_id)
    return payments.get(conn, attempt_id)


# Back to HELD so the customer can retry while the hold is valid.
def fail_attempt(conn: sqlite3.Connection, attempt_id: str, reason: str) -> None:
    payment_attempt = payments.get(conn, attempt_id)
    if payment_attempt["status"] != "pending":
        return
    payments.set_status(conn, attempt_id, "failed")
    expire_due(conn, payment_attempt["booking_id"])
    bookings.transition(conn, payment_attempt["booking_id"], (states.PAYMENT_PENDING,), states.HELD,
                        "payment_failed", reason)
