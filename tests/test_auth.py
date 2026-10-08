from datetime import datetime, timedelta, timezone
from pathlib import Path

NONEXISTENT_REF = "TPK-ZZZZZZ"


# Includes the WAL/SHM files.
def database_bytes(api) -> bytes:
    db_dir = Path(api.settings.db_path).parent
    return b"".join(db_file.read_bytes() for db_file in db_dir.glob("test.db*"))


def test_missing_and_garbage_tokens_get_401(api):
    assert api.http.get("/v1/fares").status_code == 401
    for authorization in ("Bearer nope", "Bearer tpk_kdeadbeef_wrong", "Bearer ctk_wrong", "Basic abc"):
        response = api.http.get("/v1/fares", headers={"Authorization": authorization})
        assert response.status_code == 401, authorization


def test_partner_key_tampered_secret_is_rejected(api):
    tampered_key = api.partner_key[:-2] + "xx"
    assert api.http.get("/v1/fares", headers=api.auth_headers(tampered_key)).status_code == 401


def test_missing_scope_gets_403(api):
    response = api.post_hold(token=api.read_only_key)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    assert api.http.get("/v1/fares", headers=api.auth_headers(api.read_only_key)).status_code == 200


def test_partner_keys_are_not_stored_in_plaintext(api):
    secret = api.partner_key.split("_", 2)[2]
    stored_bytes = database_bytes(api)
    assert secret.encode() not in stored_bytes
    assert api.partner_key.encode() not in stored_bytes


def test_customer_tokens_are_not_stored_in_plaintext(api):
    booking = api.create_hold()
    customer_token = api.mint_customer_token(booking["ref"])["token"]
    assert customer_token.encode() not in database_bytes(api)


def test_other_partners_booking_is_404_identical_to_nonexistent(api):
    booking = api.create_hold()
    foreign_response = api.get_booking_response(booking["ref"], token=api.rival_partner_key)
    missing_response = api.get_booking_response(NONEXISTENT_REF, token=api.rival_partner_key)
    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()
    assert api.post_cancel(booking["ref"], token=api.rival_partner_key).status_code == 404
    assert api.post_payment(booking["ref"], token=api.rival_partner_key).status_code == 404
    assert api.get_booking(booking["ref"])["state"] == "held"


def test_customer_token_reads_and_pays_its_own_booking(api):
    booking = api.create_hold()
    customer_token = api.mint_customer_token(booking["ref"])["token"]
    response = api.get_booking_response(booking["ref"], token=customer_token)
    assert response.status_code == 200
    assert "events" not in response.json()  # audit trail is partner-only
    assert api.post_payment(booking["ref"], token=customer_token).status_code == 202


def test_customer_token_on_another_booking_is_404_identical_to_nonexistent(api):
    own_booking, other_booking = api.create_hold(), api.create_hold()
    customer_token = api.mint_customer_token(own_booking["ref"])["token"]
    foreign_response = api.get_booking_response(other_booking["ref"], token=customer_token)
    missing_response = api.get_booking_response(NONEXISTENT_REF, token=customer_token)
    assert foreign_response.status_code == missing_response.status_code == 404
    assert foreign_response.json() == missing_response.json()
    assert api.post_cancel(other_booking["ref"], token=customer_token).status_code == 404
    assert api.get_booking(other_booking["ref"])["state"] == "held"


def test_customer_token_cannot_list_fares_or_mint_tokens(api):
    booking = api.create_hold()
    customer_token = api.mint_customer_token(booking["ref"])["token"]
    assert api.http.get("/v1/fares", headers=api.auth_headers(customer_token)).status_code == 403
    mint_response = api.http.post(f"/v1/bookings/{booking['ref']}/customer-token",
                                  headers=api.auth_headers(customer_token))
    assert mint_response.status_code == 403


def test_customer_token_expires_after_24h(api):
    booking = api.create_hold()
    minted = api.mint_customer_token(booking["ref"])
    expires_at = datetime.fromisoformat(minted["expires_at"].replace("Z", "+00:00"))
    assert abs(expires_at - (datetime.now(timezone.utc) + timedelta(hours=24))) < timedelta(seconds=10)
    api.execute_sql("UPDATE customer_tokens SET expires_at = 0")  # fast-forward past expiry
    assert api.get_booking_response(booking["ref"], token=minted["token"]).status_code == 401


def test_revoked_partner_key_is_rejected(api):
    key_id = api.partner_key.split("_")[1]
    api.execute_sql("UPDATE api_keys SET revoked_at = 1 WHERE id = ?", (key_id,))
    assert api.http.get("/v1/fares", headers=api.auth_headers()).status_code == 401
