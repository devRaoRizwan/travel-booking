# Partner key: tpk_<key_id>_<secret>. Customer token: ctk_<secret>, 24h, one booking. Both stored hashed.

import secrets
import sqlite3

from app.domain.errors import NotFound, Unauthenticated
from app.domain.principal import ALL_SCOPES, CUSTOMER_SCOPES, Principal
from app.infrastructure.clock import now_ms
from app.infrastructure.database import write_tx
from app.infrastructure.security import hash_secret, new_secret, secrets_match
from app.repositories import bookings, credentials


def create_partner(conn: sqlite3.Connection, partner_id: str, name: str) -> None:
    with write_tx(conn):
        credentials.insert_partner(conn, partner_id, name)


# Plaintext is returned once; only the hash is stored.
def create_partner_key(conn: sqlite3.Connection, partner_id: str, scopes) -> str:
    requested_scopes = set(scopes)
    unknown_scopes = requested_scopes - ALL_SCOPES
    if unknown_scopes:
        raise ValueError(f"unknown scopes: {sorted(unknown_scopes)}")
    key_id = "k" + secrets.token_hex(6)
    secret = new_secret()
    with write_tx(conn):
        credentials.insert_api_key(conn, key_id, partner_id, hash_secret(secret), requested_scopes)
    return f"tpk_{key_id}_{secret}"


# Call inside write_tx.
def issue_customer_token(conn: sqlite3.Connection, booking_id: int, ttl_s: float) -> tuple[str, int]:
    token = "ctk_" + new_secret()
    expires_at = now_ms() + int(ttl_s * 1000)
    credentials.insert_customer_token(conn, hash_secret(token), booking_id, expires_at)
    return token, expires_at


def authenticate(conn: sqlite3.Connection, token: str | None) -> Principal:
    if not token:
        raise Unauthenticated("UNAUTHENTICATED", "missing bearer token")

    if token.startswith("tpk_"):
        token_parts = token.split("_", 2)
        api_key = credentials.get_api_key(conn, token_parts[1]) if len(token_parts) == 3 else None
        if (api_key is None or api_key["revoked_at"] is not None
                or not secrets_match(api_key["secret_hash"], token_parts[2])):
            raise Unauthenticated("UNAUTHENTICATED", "invalid credential")
        return Principal("partner", api_key["partner_id"], frozenset(api_key["scopes"].split()))

    if token.startswith("ctk_"):
        customer_token = credentials.get_customer_token(conn, hash_secret(token))
        if customer_token is None or customer_token["expires_at"] <= now_ms():
            raise Unauthenticated("UNAUTHENTICATED", "invalid or expired credential")
        return Principal("customer", customer_token["partner_id"], CUSTOMER_SCOPES, customer_token["booking_id"])

    raise Unauthenticated("UNAUTHENTICATED", "invalid credential")


# Same 404 for missing and not-visible bookings.
def load_visible_booking(conn: sqlite3.Connection, principal: Principal, ref: str) -> sqlite3.Row:
    booking = bookings.get_by_ref(conn, ref)
    if booking is None or not principal.can_see(booking):
        raise NotFound("BOOKING_NOT_FOUND", "booking not found")
    return booking
