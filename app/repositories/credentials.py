import sqlite3

from app.infrastructure.clock import now_ms


def insert_partner(conn: sqlite3.Connection, partner_id: str, name: str) -> None:
    conn.execute("INSERT OR IGNORE INTO partners (id, name, created_at) VALUES (?,?,?)", (partner_id, name, now_ms()))


def insert_api_key(conn: sqlite3.Connection, key_id: str, partner_id: str, secret_hash: str, scopes: set) -> None:
    conn.execute(
        "INSERT INTO api_keys (id, partner_id, secret_hash, scopes, created_at) VALUES (?,?,?,?,?)",
        (key_id, partner_id, secret_hash, " ".join(sorted(scopes)), now_ms()),
    )


def get_api_key(conn: sqlite3.Connection, key_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT partner_id, secret_hash, scopes, revoked_at FROM api_keys WHERE id = ?", (key_id,)
    ).fetchone()


def insert_customer_token(conn: sqlite3.Connection, token_hash: str, booking_id: int, expires_at: int) -> None:
    conn.execute(
        "INSERT INTO customer_tokens (token_hash, booking_id, expires_at, created_at) VALUES (?,?,?,?)",
        (token_hash, booking_id, expires_at, now_ms()),
    )


def get_customer_token(conn: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT token.booking_id, token.expires_at, booking.partner_id
           FROM customer_tokens token JOIN bookings booking ON booking.id = token.booking_id
           WHERE token.token_hash = ?""",
        (token_hash,),
    ).fetchone()
