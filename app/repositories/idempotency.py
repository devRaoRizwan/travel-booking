import json
import sqlite3

from app.infrastructure.clock import now_ms


def get(conn: sqlite3.Connection, scope: str, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM idempotency_keys WHERE principal = ? AND key = ?", (scope, key)).fetchone()


def insert(conn: sqlite3.Connection, scope: str, key: str, request_hash: str, resource_id: str | None = None,
           status: int | None = None, body: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO idempotency_keys (principal, key, request_hash, resource_id,
               response_status, response_body, created_at) VALUES (?,?,?,?,?,?,?)""",
        (scope, key, request_hash, resource_id, status, json.dumps(body) if body is not None else None, now_ms()),
    )


# First writer wins.
def store_response(conn: sqlite3.Connection, scope: str, key: str, status: int, body: dict) -> None:
    conn.execute(
        """UPDATE idempotency_keys SET response_status = ?, response_body = ?
           WHERE principal = ? AND key = ? AND response_status IS NULL""",
        (status, json.dumps(body), scope, key),
    )
