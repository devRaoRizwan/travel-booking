# Webhook header: PSP-Signature: t=<unix>,v1=HMAC-SHA256(secret, "<t>." + raw_body).

import hashlib
import hmac
import secrets
import time

from app.domain.errors import Unauthenticated


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def secrets_match(stored_hash: str, presented_secret: str) -> bool:
    return hmac.compare_digest(stored_hash, hash_secret(presented_secret))


def verify_psp_signature(secret: str, signature_header: str | None, raw_body: bytes, tolerance_s: float) -> None:
    if not signature_header:
        raise Unauthenticated("INVALID_SIGNATURE", "missing PSP-Signature header")
    try:
        header_fields = dict(field.split("=", 1) for field in signature_header.split(","))
        timestamp = int(header_fields["t"])
        signature = header_fields["v1"]
    except (ValueError, KeyError):
        raise Unauthenticated("INVALID_SIGNATURE", "malformed PSP-Signature header")
    expected_signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        raise Unauthenticated("INVALID_SIGNATURE", "signature mismatch")
    if abs(time.time() - timestamp) > tolerance_s:
        raise Unauthenticated("INVALID_SIGNATURE", "timestamp outside tolerance")
