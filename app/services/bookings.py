import sqlite3

from app.config import Settings
from app.domain.principal import Principal
from app.infrastructure.clock import iso
from app.infrastructure.database import write_tx
from app.repositories import bookings
from app.services import booking_view
from app.services.auth import issue_customer_token, load_visible_booking
from app.services.expiry import expire_due


def read_booking(conn: sqlite3.Connection, principal: Principal, ref: str) -> dict:
    principal.require("bookings:read")
    with write_tx(conn):  # write tx: lazy expiry may update the row
        booking = load_visible_booking(conn, principal, ref)
        expire_due(conn, booking["id"])
        return booking_view.render(conn, bookings.get(conn, booking["id"]), include_events=principal.is_partner)


def mint_customer_token(conn: sqlite3.Connection, settings: Settings, principal: Principal, ref: str) -> dict:
    principal.require("bookings:write")
    with write_tx(conn):
        booking = load_visible_booking(conn, principal, ref)
        token, expires_at = issue_customer_token(conn, booking["id"], settings.customer_token_ttl_s)
    return {"token": token, "booking_ref": ref, "expires_at": iso(expires_at)}
