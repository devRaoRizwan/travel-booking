import secrets
import sqlite3
from datetime import date, timedelta

from app.config import Settings
from app.domain import states
from app.domain.errors import Conflict, NotFound, Unprocessable
from app.domain.principal import Principal
from app.infrastructure.clock import iso, now_ms
from app.infrastructure.database import write_tx
from app.repositories import bookings, fares
from app.repositories import idempotency as idempotency_repo
from app.services import booking_view, idempotency
from app.services.commands import HoldRequest

REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # excludes 0/O and 1/I
REF_LENGTH = 6
PASSPORT_MIN_VALIDITY = timedelta(days=182)  # UAE/KSA entry: 6 months beyond travel date


def create_hold(conn: sqlite3.Connection, settings: Settings, principal: Principal, hold_request: HoldRequest,
                idempotency_key: str | None) -> idempotency.Outcome:
    principal.require("holds:write")
    request_body = hold_request.model_dump(mode="json")
    body_hash = idempotency.request_hash("POST", "/v1/holds", request_body)
    with write_tx(conn):
        if idempotency_key:
            record = idempotency.lookup(conn, principal.idempotency_scope, idempotency_key, body_hash)
            if stored_outcome := idempotency.replay(record):
                return stored_outcome

        fare = fares.get(conn, hold_request.fare_id)
        if fare is None:
            raise NotFound("FARE_NOT_FOUND", "fare not found")
        _validate_documents(hold_request, fare)

        seat_count = len(hold_request.passengers)
        if not fares.try_reserve_seats(conn, fare["id"], seat_count):
            raise Conflict("SOLD_OUT", f"fewer than {seat_count} seat(s) left on this fare")

        fare_taxes = fares.taxes(conn, fare["id"])
        now = now_ms()
        booking_id = bookings.insert(
            conn,
            ref=_generate_ref(conn),
            partner_id=principal.partner_id,
            fare=fare,
            state=states.HELD,
            passengers=request_body["passengers"],
            contact=request_body["contact"],
            consent=request_body["consent"] | {"accepted_at": iso(now)},
            per_passenger_pkr=fare["base_fare_pkr"] + sum(tax["amount_pkr"] for tax in fare_taxes),
            hold_expires_at=now + int(settings.hold_ttl_s * 1000),
            created_at=now,
        )
        bookings.insert_price_lines(conn, booking_id, fare, fare_taxes)
        view = booking_view.render(conn, bookings.get(conn, booking_id))
        if idempotency_key:
            idempotency_repo.insert(conn, principal.idempotency_scope, idempotency_key, body_hash,
                                    resource_id=view["ref"], status=201, body=view)
    return idempotency.Outcome(201, view)


# Check-then-insert is safe under the write lock.
def _generate_ref(conn: sqlite3.Connection) -> str:
    while True:
        ref = "TPK-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(REF_LENGTH))
        if not bookings.ref_exists(conn, ref):
            return ref


def _validate_documents(hold_request: HoldRequest, fare) -> None:
    departure_date = date.fromisoformat(fare["depart_at"][:10])
    for index, passenger in enumerate(hold_request.passengers):
        document = passenger.document
        if fare["international"]:
            if document.type != "passport":
                raise Unprocessable("DOCUMENT_REQUIRED",
                                    f"passengers[{index}]: international travel requires a passport")
            if document.expires_on is None or document.expires_on < departure_date + PASSPORT_MIN_VALIDITY:
                raise Unprocessable("DOCUMENT_EXPIRES_TOO_SOON",
                                    f"passengers[{index}]: passport must be valid 6 months beyond departure")
        if passenger.date_of_birth >= departure_date:
            raise Unprocessable("VALIDATION_ERROR", f"passengers[{index}]: date_of_birth must be before departure")
