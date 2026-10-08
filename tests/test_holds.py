import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from tests.conftest import CONSENT, CONTACT, PASSENGER, wait_until

LAST_SEAT_FARE = "F-ISB-JED-SV723"  # seeded with seats_left = 1


def test_hold_decrements_seats_snapshots_price_and_returns_ref_and_20min_expiry(api):
    seats_before = api.seats_left("F-LHE-KHI-PK302")
    requested_at = time.time()
    response = api.post_hold("F-LHE-KHI-PK302", passenger_count=2)
    assert response.status_code == 201
    booking = response.json()

    assert re.fullmatch(r"TPK-[A-Z2-9]{6}", booking["ref"])
    assert booking["state"] == "held"
    assert api.seats_left("F-LHE-KHI-PK302") == seats_before - 2
    # 16950 + 2000 + 500 + 3000 = 22450 per passenger, x2
    assert booking["price"]["per_passenger_total_pkr"] == 22450
    assert booking["price"]["total_pkr"] == 44900
    expires_at = datetime.fromisoformat(booking["hold_expires_at"].replace("Z", "+00:00")).timestamp()
    assert abs(expires_at - (requested_at + 20 * 60)) < 5


def test_price_snapshot_survives_later_fare_change(api):
    booking = api.create_hold("F-LHE-KHI-PK302")
    api.execute_sql("UPDATE fares SET base_fare_pkr = base_fare_pkr * 2 WHERE id = 'F-LHE-KHI-PK302'")
    assert api.get_booking(booking["ref"])["price"]["total_pkr"] == 22450
    payment_response = api.post_payment(booking["ref"])
    assert payment_response.json()["amount_pkr"] == 22450


def test_two_holds_racing_for_last_seat_exactly_one_wins(api):
    assert api.seats_left(LAST_SEAT_FARE) == 1
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: api.post_hold(LAST_SEAT_FARE), range(10)))
    status_codes = sorted(response.status_code for response in responses)
    assert status_codes == [201] + [409] * 9
    assert all(response.json()["error"]["code"] == "SOLD_OUT"
               for response in responses if response.status_code == 409)
    assert api.seats_left(LAST_SEAT_FARE) == 0


def test_multi_passenger_hold_needs_enough_seats(api):
    response = api.post_hold("F-LHE-KHI-PA401", passenger_count=5)  # 4 seats
    assert response.status_code == 409
    assert api.seats_left("F-LHE-KHI-PA401") == 4


def test_international_hold_requires_passport_valid_six_months(api):
    cnic_passenger = {**PASSENGER, "document": {"type": "cnic", "number": "3520212345671", "issuing_country": "PK"}}
    response = api.http.post("/v1/holds", headers=api.auth_headers(), json={
        "fare_id": "F-KHI-DXB-EK601", "passengers": [cnic_passenger], "contact": CONTACT, "consent": CONSENT})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DOCUMENT_REQUIRED"

    expiring_passenger = {**PASSENGER, "document": {**PASSENGER["document"], "expires_on": "2027-01-01"}}
    response = api.http.post("/v1/holds", headers=api.auth_headers(), json={
        "fare_id": "F-KHI-DXB-EK601", "passengers": [expiring_passenger], "contact": CONTACT, "consent": CONSENT})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DOCUMENT_EXPIRES_TOO_SOON"
    assert api.seats_left("F-KHI-DXB-EK601") == 7


def test_hold_with_idempotency_key_replays_without_taking_another_seat(api):
    idempotency_header = {"Idempotency-Key": str(uuid.uuid4())}
    first_response = api.post_hold("F-LHE-KHI-PA401", extra_headers=idempotency_header)
    replayed_response = api.post_hold("F-LHE-KHI-PA401", extra_headers=idempotency_header)
    assert first_response.status_code == replayed_response.status_code == 201
    assert first_response.json()["ref"] == replayed_response.json()["ref"]
    assert replayed_response.headers.get("Idempotent-Replayed") == "true"
    assert api.seats_left("F-LHE-KHI-PA401") == 3


@pytest.mark.settings(hold_ttl_s=0.5)
def test_expired_hold_releases_seat_via_sweeper(api):
    booking = api.create_hold(LAST_SEAT_FARE)
    assert api.seats_left(LAST_SEAT_FARE) == 0
    # No API reads of the booking: only the background sweeper can expire it.
    wait_until(lambda: api.seats_left(LAST_SEAT_FARE) == 1, timeout=3)
    assert api.booking_state_in_db(booking["ref"]) == "expired"
    assert api.post_hold(LAST_SEAT_FARE).status_code == 201


@pytest.mark.settings(hold_ttl_s=0.3, worker_interval_s=60)
def test_expiry_is_enforced_on_read_even_if_sweeper_is_late(api):
    booking = api.create_hold(LAST_SEAT_FARE)
    time.sleep(0.5)
    assert api.get_booking(booking["ref"])["state"] == "expired"
    assert api.seats_left(LAST_SEAT_FARE) == 1


@pytest.mark.settings(hold_ttl_s=0.3, worker_interval_s=60)
def test_concurrent_reads_of_expired_hold_release_seats_once(api):
    booking = api.create_hold("F-LHE-KHI-PA401", passenger_count=2)
    time.sleep(0.5)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: api.get_booking_response(booking["ref"]), range(8)))
    assert api.seats_left("F-LHE-KHI-PA401") == 4


def test_hold_without_fare_rules_consent_is_rejected(api):
    hold_body = {"fare_id": "F-LHE-KHI-PK302", "passengers": [PASSENGER], "contact": CONTACT,
                 "consent": {"terms_version": "tos-2.1", "fare_rules_accepted": False}}
    response = api.http.post("/v1/holds", json=hold_body, headers=api.auth_headers())
    assert response.status_code == 422
    assert api.seats_left("F-LHE-KHI-PK302") == 9


def test_hold_records_consent_with_server_timestamp(api):
    booking = api.create_hold()
    assert booking["consent"]["terms_version"] == "tos-2.1"
    assert booking["consent"]["fare_rules_accepted"] is True
    assert booking["consent"]["accepted_at"] == booking["created_at"]
