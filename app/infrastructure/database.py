import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS partners (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

-- Partner API keys; secret stored as SHA-256 hex digest only.
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,           -- public key id: 'k' + 12 hex chars
    partner_id   TEXT NOT NULL REFERENCES partners(id),
    secret_hash  TEXT NOT NULL,
    scopes       TEXT NOT NULL,              -- space separated
    created_at   INTEGER NOT NULL,
    revoked_at   INTEGER
);

CREATE TABLE IF NOT EXISTS fares (
    id               TEXT PRIMARY KEY,
    carrier          TEXT NOT NULL,
    flight_no        TEXT NOT NULL,
    origin           TEXT NOT NULL,
    destination      TEXT NOT NULL,
    depart_at        TEXT NOT NULL,
    cabin            TEXT NOT NULL,
    fare_basis       TEXT NOT NULL,
    international    INTEGER NOT NULL,
    base_fare_pkr  INTEGER NOT NULL CHECK (base_fare_pkr >= 0),
    seats_left       INTEGER NOT NULL CHECK (seats_left >= 0),
    refundable       INTEGER NOT NULL,
    cancel_fee_pkr INTEGER NOT NULL CHECK (cancel_fee_pkr >= 0),
    change_fee_pkr INTEGER,
    baggage          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fare_taxes (
    fare_id      TEXT NOT NULL REFERENCES fares(id),
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,
    amount_pkr INTEGER NOT NULL CHECK (amount_pkr >= 0),
    refundable   INTEGER NOT NULL,
    PRIMARY KEY (fare_id, code)
);

CREATE TABLE IF NOT EXISTS bookings (
    id                    INTEGER PRIMARY KEY,
    ref                   TEXT NOT NULL UNIQUE,
    partner_id            TEXT NOT NULL REFERENCES partners(id),
    fare_id               TEXT NOT NULL REFERENCES fares(id),
    state                 TEXT NOT NULL,
    pax_count             INTEGER NOT NULL CHECK (pax_count > 0),
    passengers_json       TEXT NOT NULL,
    contact_json          TEXT NOT NULL,
    consent_json          TEXT NOT NULL,     -- {terms_version, fare_rules_accepted, accepted_at}
    -- price snapshot at hold time; immutable
    refundable            INTEGER NOT NULL,
    cancel_fee_pkr      INTEGER NOT NULL,
    per_passenger_pkr         INTEGER NOT NULL,
    total_pkr           INTEGER NOT NULL CHECK (total_pkr >= 0),
    hold_expires_at       INTEGER NOT NULL,  -- epoch ms
    pnr                   TEXT,
    ticketing_attempts    INTEGER NOT NULL DEFAULT 0,
    ticketing_next_at     INTEGER,
    ticketing_lease_until INTEGER,
    last_error            TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_expiry ON bookings(state, hold_expires_at);

-- Per-passenger price lines copied from fare + fare_taxes at hold time.
CREATE TABLE IF NOT EXISTS booking_price_lines (
    booking_id   INTEGER NOT NULL REFERENCES bookings(id),
    kind         TEXT NOT NULL CHECK (kind IN ('base', 'tax')),
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,
    amount_pkr INTEGER NOT NULL CHECK (amount_pkr >= 0),
    refundable   INTEGER NOT NULL,
    PRIMARY KEY (booking_id, code)
);

CREATE TABLE IF NOT EXISTS customer_tokens (
    token_hash  TEXT PRIMARY KEY,
    booking_id  INTEGER NOT NULL REFERENCES bookings(id),
    expires_at  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_attempts (
    id              TEXT PRIMARY KEY,         -- PSP merchant_reference and Idempotency-Key
    booking_id      INTEGER NOT NULL REFERENCES bookings(id),
    amount_pkr    INTEGER NOT NULL CHECK (amount_pkr > 0),
    method          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    psp_payment_id  TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
-- At most one pending payment attempt per booking.
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_payment
    ON payment_attempts(booking_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS refunds (
    id                 TEXT PRIMARY KEY,      -- PSP Idempotency-Key
    booking_id         INTEGER NOT NULL REFERENCES bookings(id),
    payment_attempt_id TEXT NOT NULL UNIQUE REFERENCES payment_attempts(id),
    amount_pkr       INTEGER NOT NULL CHECK (amount_pkr >= 0),
    reason             TEXT NOT NULL,
    breakdown_json     TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending', 'succeeded')),
    psp_refund_id      TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_at            INTEGER NOT NULL,
    created_at         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    principal        TEXT NOT NULL,
    key              TEXT NOT NULL,
    request_hash     TEXT NOT NULL,
    resource_id      TEXT,                    -- e.g. payment_attempts.id
    response_status  INTEGER,                 -- NULL until the final response is stored
    response_body    TEXT,
    created_at       INTEGER NOT NULL,
    PRIMARY KEY (principal, key)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id     TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    received_at  INTEGER NOT NULL,
    payload      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS booking_events (
    id          INTEGER PRIMARY KEY,
    booking_id  INTEGER NOT NULL REFERENCES bookings(id),
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    event       TEXT NOT NULL,
    detail      TEXT,
    at          INTEGER NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None disables implicit transactions; write_tx issues
    # BEGIN IMMEDIATE so the write lock is acquired at transaction start.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


# BEGIN IMMEDIATE takes the write lock at transaction start.
@contextmanager
def write_tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
