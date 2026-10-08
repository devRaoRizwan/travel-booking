import time

from tests.conftest import wait_until

LAST_SEAT_FARE = "F-ISB-JED-SV723"


def pay_and_wait_for_state(api, expected_state, fare_id="F-LHE-KHI-PK302", timeout=6) -> dict:
    booking = api.create_hold(fare_id)
    assert api.post_payment(booking["ref"]).status_code == 202
    return api.wait_for_state(booking["ref"], expected_state, timeout)


def test_airline_timeout_then_retry_issues_exactly_one_pnr(api):
    # Attempt 1: airline sleeps 1.0s vs client timeout 0.5s but has already created the PNR.
    api.configure_airline(mode="slow", count=1)
    ticketed = pay_and_wait_for_state(api, "ticketed")
    issued_pnrs = api.pnrs_for_booking(ticketed["ref"])
    assert len(issued_pnrs) == 1
    assert ticketed["ticket"]["pnr"] == issued_pnrs[0]["pnr"]
    assert ticketed["ticketing"]["attempts"] == 2
    assert any(event["event"] == "ticketing_retry_scheduled" and "timeout" in event["detail"]
               for event in ticketed["events"])


def test_flaky_airline_503s_are_retried_until_issued(api):
    api.configure_airline(mode="flaky", count=2)
    ticketed = pay_and_wait_for_state(api, "ticketed")
    assert ticketed["ticketing"]["attempts"] == 3
    assert len(api.pnrs_for_booking(ticketed["ref"])) == 1


def test_airline_500_after_issuing_is_retried_and_returns_the_same_pnr(api):
    api.configure_airline(mode="error_after_issue", count=1)
    ticketed = pay_and_wait_for_state(api, "ticketed")
    issued_pnrs = api.pnrs_for_booking(ticketed["ref"])
    assert len(issued_pnrs) == 1
    assert ticketed["ticket"]["pnr"] == issued_pnrs[0]["pnr"]


def test_airline_rejection_fails_ticketing_and_refunds_in_full(api):
    api.configure_airline(mode="reject", count=1)
    failed = pay_and_wait_for_state(api, "ticketing_failed")
    refunded = api.wait_for_refund_succeeded(failed["ref"])
    assert refunded["refund"]["reason"] == "ticketing_rejected"
    assert refunded["refund"]["amount_pkr"] == refunded["price"]["total_pkr"]
    assert refunded["ticketing"]["attempts"] == 1


def test_exhausted_retries_on_ambiguous_failures_go_to_review_without_refund(api):
    api.configure_airline(mode="slow", count=None)  # every call times out
    in_review = pay_and_wait_for_state(api, "needs_review", timeout=10)
    assert in_review["ticketing"]["attempts"] == api.settings.ticketing_max_attempts
    assert in_review["refund"] is None
    # The timed-out calls did create a PNR: the reason auto-refund is unsafe here.
    assert len(api.pnrs_for_booking(in_review["ref"])) == 1


def test_airline_stub_without_idempotency_key_duplicates_pnrs(api):
    pnr_request = {"booking_ref": "TPK-NOKEY1", "fare_id": "F-LHE-KHI-PK302", "passengers": []}
    first_pnr = api.airline.post("/pnrs", json=pnr_request).json()["pnr"]
    second_pnr = api.airline.post("/pnrs", json=pnr_request).json()["pnr"]
    assert first_pnr != second_pnr
    assert len(api.pnrs_for_booking("TPK-NOKEY1")) == 2


def test_slow_airline_does_not_delay_hold_expiry_for_other_bookings(api):
    api.configure_airline(mode="slow", count=None, slow_s=3.0)
    paid_booking = api.create_hold()
    api.post_payment(paid_booking["ref"])
    api.wait_for_state(paid_booking["ref"], "ticketing")
    unpaid_booking = api.create_hold(LAST_SEAT_FARE)
    api.execute_sql("UPDATE bookings SET hold_expires_at = 0 WHERE ref = ?", (unpaid_booking["ref"],))
    started_at = time.time()
    wait_until(lambda: api.seats_left(LAST_SEAT_FARE) == 1, timeout=2)
    assert time.time() - started_at < 1.0
