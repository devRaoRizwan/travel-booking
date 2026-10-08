import json
import sqlite3

from app.infrastructure.clock import now_ms


def get(conn: sqlite3.Connection, booking_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()


def get_by_ref(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM bookings WHERE ref = ?", (ref,)).fetchone()


def ref_exists(conn: sqlite3.Connection, ref: str) -> bool:
    return conn.execute("SELECT 1 FROM bookings WHERE ref = ?", (ref,)).fetchone() is not None


def insert(conn: sqlite3.Connection, *, ref, partner_id, fare, state, passengers, contact, consent,
           per_passenger_pkr, hold_expires_at, created_at) -> int:
    cursor = conn.execute(
        """INSERT INTO bookings (ref, partner_id, fare_id, state, pax_count, passengers_json, contact_json,
               consent_json, refundable, cancel_fee_pkr, per_passenger_pkr, total_pkr, hold_expires_at,
               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ref, partner_id, fare["id"], state, len(passengers), json.dumps(passengers), json.dumps(contact),
         json.dumps(consent), fare["refundable"], fare["cancel_fee_pkr"], per_passenger_pkr,
         per_passenger_pkr * len(passengers), hold_expires_at, created_at, created_at),
    )
    booking_id = cursor.lastrowid
    log_event(conn, booking_id, None, state, "hold_created")
    return booking_id


def insert_price_lines(conn: sqlite3.Connection, booking_id: int, fare: sqlite3.Row, fare_taxes: list) -> None:
    conn.execute("INSERT INTO booking_price_lines VALUES (?, 'base', 'BASE', 'Base fare', ?, ?)",
                 (booking_id, fare["base_fare_pkr"], fare["refundable"]))
    for tax in fare_taxes:
        conn.execute("INSERT INTO booking_price_lines VALUES (?, 'tax', ?, ?, ?, ?)",
                     (booking_id, tax["code"], tax["name"], tax["amount_pkr"], tax["refundable"]))


def price_lines(conn: sqlite3.Connection, booking_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT kind, code, name, amount_pkr, refundable FROM booking_price_lines "
        "WHERE booking_id = ? ORDER BY kind, code", (booking_id,)
    ).fetchall()


# State-guarded update plus event log. Returns False if not in from_states. Call inside write_tx.
def transition(conn: sqlite3.Connection, booking_id: int, from_states: tuple, to_state: str, event: str,
               detail: str | None = None, **fields) -> bool:
    current_state = get(conn, booking_id)["state"]
    if current_state not in from_states:
        return False
    extra_assignments = "".join(f", {column} = ?" for column in fields)
    conn.execute(
        f"UPDATE bookings SET state = ?, updated_at = ?{extra_assignments} WHERE id = ? AND state = ?",
        (to_state, now_ms(), *fields.values(), booking_id, current_state),
    )
    log_event(conn, booking_id, current_state, to_state, event, detail)
    return True


def update_fields(conn: sqlite3.Connection, booking_id: int, **fields) -> None:
    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(f"UPDATE bookings SET {assignments}, updated_at = ? WHERE id = ?",
                 (*fields.values(), now_ms(), booking_id))


def log_event(conn: sqlite3.Connection, booking_id: int, from_state: str | None, to_state: str, event: str,
              detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO booking_events (booking_id, from_state, to_state, event, detail, at) VALUES (?,?,?,?,?,?)",
        (booking_id, from_state, to_state, event, detail, now_ms()),
    )


def events(conn: sqlite3.Connection, booking_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM booking_events WHERE booking_id = ? ORDER BY id", (booking_id,)).fetchall()


def due_for_expiry(conn: sqlite3.Connection, states: tuple, booking_id: int | None = None) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(states))
    query = f"SELECT * FROM bookings WHERE state IN ({placeholders}) AND hold_expires_at <= ?"
    params: list = [*states, now_ms()]
    if booking_id is not None:
        query += " AND id = ?"
        params.append(booking_id)
    return conn.execute(query, params).fetchall()


# Lease due bookings and increment attempts. Call inside write_tx.
def claim_due_ticketing(conn: sqlite3.Connection, state: str, lease_ms: int, limit: int) -> list[int]:
    now = now_ms()
    booking_ids = [row["id"] for row in conn.execute(
        """SELECT id FROM bookings WHERE state = ? AND ticketing_next_at <= ?
           AND (ticketing_lease_until IS NULL OR ticketing_lease_until < ?)
           ORDER BY ticketing_next_at LIMIT ?""", (state, now, now, limit))]
    for booking_id in booking_ids:
        conn.execute("UPDATE bookings SET ticketing_lease_until = ?, ticketing_attempts = ticketing_attempts + 1 "
                     "WHERE id = ?", (now + lease_ms, booking_id))
    return booking_ids
