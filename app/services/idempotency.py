# Same key + same body: replay. Same key + different body: 422. Same key, no stored response: resume.

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass

from app.domain.errors import BadRequest, Unprocessable
from app.repositories import idempotency as idempotency_repo

KEY_PATTERN = re.compile(r"^[\x21-\x7e]{8,255}$")


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict
    replayed: bool = False


def validate_key(key: str | None, required: bool) -> str | None:
    if key is None:
        if required:
            raise BadRequest("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
        return None
    if not KEY_PATTERN.match(key):
        raise BadRequest("IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 8-255 printable ASCII characters")
    return key


def request_hash(method: str, path: str, body: dict) -> str:
    canonical = json.dumps([method, path, body], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def lookup(conn: sqlite3.Connection, scope: str, key: str, expected_hash: str) -> sqlite3.Row | None:
    record = idempotency_repo.get(conn, scope, key)
    if record is not None and record["request_hash"] != expected_hash:
        raise Unprocessable("IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was already used with a different request")
    return record


def replay(record: sqlite3.Row | None) -> Outcome | None:
    if record is None or record["response_status"] is None:
        return None
    return Outcome(record["response_status"], json.loads(record["response_body"]), replayed=True)
