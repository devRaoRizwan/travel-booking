import json
import sqlite3

from app.infrastructure.clock import now_ms


# False if this event id was already processed.
def record_once(conn: sqlite3.Connection, event: dict) -> bool:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO webhook_events (event_id, type, received_at, payload) VALUES (?,?,?,?)",
        (event["id"], event["type"], now_ms(), json.dumps(event)),
    )
    return cursor.rowcount == 1
