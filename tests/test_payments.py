import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from stubs.psp import sign
from tests.conftest import WEBHOOK_SECRET

LAST_SEAT_FARE = "F-ISB-JED-SV723"


def payment_attempts_for(api, ref):
    conn = api.open_db()
    try:
        return conn.execute(
            """SELECT attempt.* FROM payment_attempts attempt
               JOIN bookings booking ON booking.id = attempt.booking_id WHERE booking.ref = ?""", (ref,)
        ).fetchall()
    finally:
        conn.close()


def psp_payments_for_attempt(api, attempt_id):
    return [payment for payment in api.psp_state()["payments"] if payment["merchant_reference"] == attempt_id]


def post_raw_webhook(api, event: dict, signature_header: str) -> httpx.Response:
    return httpx.post(api.url + "/v1/webhooks/psp", content=json.dumps(event).encode(),
                      headers={"PSP-Signature": signature_header, "Content-Type": "application/json"})


def start_payment_with_manual_webhook(api, fare_id="F-LHE-KHI-PK302") -> tuple[dict, str]:
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold(fare_id)
    psp_payment_id = api.post_payment(booking["ref"]).json()["psp_payment_id"]
    return booking, psp_payment_id


def test_payment_requires_idempotency_key(api):
    booking = api.create_hold()
    response = api.post_payment(booking["ref"], idempotency_key=False)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert payment_attempts_for(api, booking["ref"]) == []


def test_happy_path_hold_pay_webhook_ticket(api):
    booking = api.create_hold()
    assert api.post_payment(booking["ref"]).status_code == 202
    ticketed = api.wait_for_state(booking["ref"], "ticketed")
    assert ticketed["ticket"]["pnr"]
    assert ticketed["payment"]["status"] == "succeeded"
    assert len(api.pnrs_for_booking(booking["ref"])) == 1


def test_replayed_idempotency_key_returns_same_response_and_charges_once(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    idempotency_key = str(uuid.uuid4())
    first_response = api.post_payment(booking["ref"], idempotency_key)
    replayed_response = api.post_payment(booking["ref"], idempotency_key)
    assert first_response.status_code == replayed_response.status_code == 202
    assert first_response.json() == replayed_response.json()
    assert replayed_response.headers.get("Idempotent-Replayed") == "true"
    assert len(payment_attempts_for(api, booking["ref"])) == 1
    assert len(psp_payments_for_attempt(api, first_response.json()["payment_id"])) == 1


def test_concurrent_requests_with_same_key_create_one_attempt(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    idempotency_key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: api.post_payment(booking["ref"], idempotency_key), range(6)))
    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["payment_id"] for response in responses}) == 1
    assert len(payment_attempts_for(api, booking["ref"])) == 1
    assert len(api.psp_state()["payments"]) == 1


def test_idempotency_key_reused_with_different_body_is_rejected(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    idempotency_key = str(uuid.uuid4())
    assert api.post_payment(booking["ref"], idempotency_key, method="jazzcash").status_code == 202
    response = api.post_payment(booking["ref"], idempotency_key, method="easypaisa")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert [attempt["method"] for attempt in payment_attempts_for(api, booking["ref"])] == ["jazzcash"]


def test_idempotency_key_reused_on_other_booking_is_rejected(api):
    api.configure_psp(auto_deliver=False)
    first_booking, second_booking = api.create_hold(), api.create_hold()
    idempotency_key = str(uuid.uuid4())
    assert api.post_payment(first_booking["ref"], idempotency_key).status_code == 202
    response = api.post_payment(second_booking["ref"], idempotency_key)
    assert response.status_code == 422
    assert payment_attempts_for(api, second_booking["ref"]) == []


def test_second_payment_with_new_key_while_one_pending_is_rejected(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    assert api.post_payment(booking["ref"]).status_code == 202
    response = api.post_payment(booking["ref"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_IN_PROGRESS"
    assert len(payment_attempts_for(api, booking["ref"])) == 1


def test_psp_unavailable_then_retry_with_same_key_resumes_same_attempt(api):
    api.configure_psp(fail_create=1)
    booking = api.create_hold()
    idempotency_key = str(uuid.uuid4())
    failed_response = api.post_payment(booking["ref"], idempotency_key)
    assert failed_response.status_code == 502
    assert failed_response.json()["error"]["code"] == "PSP_UNAVAILABLE"
    retried_response = api.post_payment(booking["ref"], idempotency_key)
    assert retried_response.status_code == 202
    assert len(payment_attempts_for(api, booking["ref"])) == 1
    api.wait_for_state(booking["ref"], "ticketed")


def test_duplicate_webhook_delivery_is_applied_once(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    delivery_report = api.deliver_webhook(psp_payment_id, outcome="succeeded", times=3)
    assert [delivery["status"] for delivery in delivery_report["deliveries"]] == [200, 200, 200]
    ticketed = api.wait_for_state(booking["ref"], "ticketed")
    success_transitions = [event for event in ticketed["events"] if event["event"] == "payment_succeeded"]
    assert len(success_transitions) == 1
    assert len(api.pnrs_for_booking(booking["ref"])) == 1


def test_late_success_event_after_failure_does_not_resurrect_attempt(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    api.deliver_webhook(psp_payment_id, outcome="failed")
    api.deliver_webhook(psp_payment_id, outcome="succeeded")  # new event id, contradictory outcome
    current = api.get_booking(booking["ref"])
    assert current["state"] == "held"
    assert current["payment"]["status"] == "failed"


def test_failed_payment_returns_booking_to_held_and_allows_retry(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    api.deliver_webhook(psp_payment_id, outcome="failed")
    assert api.get_booking(booking["ref"])["state"] == "held"
    api.configure_psp(auto_deliver=True)
    assert api.post_payment(booking["ref"]).status_code == 202
    api.wait_for_state(booking["ref"], "ticketed")


def test_webhook_with_bad_signature_is_rejected_and_ignored(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    delivery_report = api.deliver_webhook(psp_payment_id, outcome="succeeded", bad_signature=True)
    assert delivery_report["deliveries"][0]["status"] == 401
    assert api.get_booking(booking["ref"])["state"] == "payment_pending"


def test_webhook_with_stale_timestamp_is_rejected(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    delivery_report = api.deliver_webhook(psp_payment_id, outcome="succeeded", timestamp_offset_s=-600)
    assert delivery_report["deliveries"][0]["status"] == 401
    assert api.get_booking(booking["ref"])["state"] == "payment_pending"


def test_webhook_body_tampered_after_signing_is_rejected(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    attempt_id = api.post_payment(booking["ref"]).json()["payment_id"]
    event = {"id": "evt_tampered", "type": "payment.succeeded",
             "data": {"payment_id": "pay_x", "merchant_reference": attempt_id, "amount_pkr": 1, "currency": "PKR"}}
    signature_header = sign(WEBHOOK_SECRET, json.dumps(event).encode())
    event["data"]["amount_pkr"] = 22450
    assert post_raw_webhook(api, event, signature_header).status_code == 401


def test_webhook_amount_mismatch_goes_to_review_not_ticketing(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api)
    api.deliver_webhook(psp_payment_id, outcome="succeeded", amount_pkr=100)
    time.sleep(0.3)
    assert api.get_booking(booking["ref"])["state"] == "needs_review"
    assert api.pnrs_for_booking(booking["ref"]) == []


def test_webhook_for_unknown_payment_returns_404_so_psp_retries(api):
    event = {"id": "evt_unknown", "type": "payment.succeeded",
             "data": {"payment_id": "pay_x", "merchant_reference": "pa_nope", "amount_pkr": 1, "currency": "PKR"}}
    signature_header = sign(WEBHOOK_SECRET, json.dumps(event).encode())
    assert post_raw_webhook(api, event, signature_header).status_code == 404
    conn = api.open_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.settings(hold_ttl_s=0.6)
def test_payment_landing_after_hold_expiry_is_refunded_in_full_and_not_ticketed(api):
    booking, psp_payment_id = start_payment_with_manual_webhook(api, LAST_SEAT_FARE)
    api.wait_for_state(booking["ref"], "expired", timeout=3)
    assert api.seats_left(LAST_SEAT_FARE) == 1

    api.deliver_webhook(psp_payment_id, outcome="succeeded")
    refunded = api.wait_for_refund_succeeded(booking["ref"])
    assert refunded["state"] == "expired"
    assert refunded["refund"]["reason"] == "payment_after_expired"
    assert refunded["refund"]["amount_pkr"] == refunded["price"]["total_pkr"]
    assert api.pnrs_for_booking(booking["ref"]) == []
    psp_refund_amounts = [refund["amount_pkr"] for refund in api.psp_state()["refunds"]]
    assert psp_refund_amounts == [refunded["price"]["total_pkr"]]


@pytest.mark.settings(hold_ttl_s=0.3)
def test_cannot_start_payment_on_expired_hold(api):
    booking = api.create_hold()
    time.sleep(0.5)
    response = api.post_payment(booking["ref"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "HOLD_EXPIRED"
    assert api.psp_state()["payments"] == []


def test_real_psp_timing_0_to_2s_webhook_delay(api):
    api.configure_psp(delay_ms=[0, 2000])
    booking = api.create_hold()
    assert api.post_payment(booking["ref"]).status_code == 202
    api.wait_for_state(booking["ref"], "ticketed", timeout=5)
