import sqlite3
from datetime import datetime, timezone

from app.domain import states
from app.domain.errors import Conflict
from app.domain.principal import Principal
from app.domain.refunds import compute_refund
from app.infrastructure.database import write_tx
from app.repositories import bookings, fares, payments
from app.services import booking_view
from app.services.auth import load_visible_booking
from app.services.expiry import expire_due
from app.services.refunds import create_refund


def cancel_booking(conn: sqlite3.Connection, principal: Principal, ref: str) -> dict:
    principal.require("bookings:cancel")
    with write_tx(conn):
        booking = load_visible_booking(conn, principal, ref)
        expire_due(conn, booking["id"])
        booking = bookings.get(conn, booking["id"])
        event_name = "cancelled_by_" + principal.kind

        if booking["state"] == states.CANCELLED:
            pass
        elif booking["state"] in states.EXPIRABLE:
            bookings.transition(conn, booking["id"], states.EXPIRABLE, states.CANCELLED, event_name)
            fares.release_seats(conn, booking["fare_id"], booking["pax_count"])
        elif booking["state"] == states.TICKETED:
            _cancel_ticketed(conn, booking, event_name)
        elif booking["state"] == states.TICKETING:
            # Airline outcome pending; cancelling now would race the issue call.
            raise Conflict("TICKETING_IN_PROGRESS", "ticket issuance in progress; retry shortly", retry_after=5)
        else:
            raise Conflict("NOT_CANCELLABLE", f"booking in state {booking['state']!r} cannot be cancelled")
        return booking_view.render(conn, bookings.get(conn, booking["id"]), include_events=principal.is_partner)


def _cancel_ticketed(conn: sqlite3.Connection, booking: sqlite3.Row, event_name: str) -> None:
    departure = datetime.fromisoformat(fares.get(conn, booking["fare_id"])["depart_at"])
    if departure <= datetime.now(timezone.utc):
        raise Conflict("FLIGHT_DEPARTED", "cannot cancel after departure")
    refund_amount, breakdown = compute_refund(
        bookings.price_lines(conn, booking["id"]),
        passenger_count=booking["pax_count"],
        fare_refundable=bool(booking["refundable"]),
        cancel_fee_pkr=booking["cancel_fee_pkr"],
    )
    bookings.transition(conn, booking["id"], (states.TICKETED,), states.CANCELLED, event_name)
    create_refund(conn, bookings.get(conn, booking["id"]), payments.captured_for_booking(conn, booking["id"]),
                  refund_amount, "customer_cancellation", breakdown)
