import sqlite3

from app.domain import states
from app.repositories import bookings, fares


# Run by the sweeper and before every booking operation; the state guard releases seats exactly once.
def expire_due(conn: sqlite3.Connection, booking_id: int | None = None) -> int:
    expired_count = 0
    for booking in bookings.due_for_expiry(conn, states.EXPIRABLE, booking_id):
        if bookings.transition(conn, booking["id"], states.EXPIRABLE, states.EXPIRED, "hold_expired"):
            fares.release_seats(conn, booking["fare_id"], booking["pax_count"])
            expired_count += 1
    return expired_count
