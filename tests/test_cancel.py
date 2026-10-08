from concurrent.futures import ThreadPoolExecutor

LAST_SEAT_FARE = "F-ISB-JED-SV723"


def create_ticketed_booking(api, fare_id, passenger_count=1) -> dict:
    booking = api.create_hold(fare_id, passenger_count)
    api.post_payment(booking["ref"])
    return api.wait_for_state(booking["ref"], "ticketed")


def test_cancel_held_booking_releases_seat_with_no_refund(api):
    booking = api.create_hold(LAST_SEAT_FARE)
    response = api.post_cancel(booking["ref"])
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert response.json()["refund"] is None
    assert api.seats_left(LAST_SEAT_FARE) == 1


def test_cancel_twice_is_a_no_op(api):
    booking = api.create_hold(LAST_SEAT_FARE)
    api.post_cancel(booking["ref"])
    response = api.post_cancel(booking["ref"])
    assert response.status_code == 200
    assert api.seats_left(LAST_SEAT_FARE) == 1


def test_cancel_during_pending_payment_then_success_webhook_refunds_in_full(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold(LAST_SEAT_FARE)
    psp_payment_id = api.post_payment(booking["ref"]).json()["psp_payment_id"]

    response = api.post_cancel(booking["ref"])
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert api.seats_left(LAST_SEAT_FARE) == 1

    api.deliver_webhook(psp_payment_id, outcome="succeeded")
    refunded = api.wait_for_refund_succeeded(booking["ref"])
    assert refunded["state"] == "cancelled"
    assert refunded["refund"]["reason"] == "payment_after_cancelled"
    assert refunded["refund"]["amount_pkr"] == refunded["price"]["total_pkr"]
    assert api.pnrs_for_booking(booking["ref"]) == []


def test_cancel_during_pending_payment_then_failure_webhook_has_no_refund(api):
    api.configure_psp(auto_deliver=False)
    booking = api.create_hold()
    psp_payment_id = api.post_payment(booking["ref"]).json()["psp_payment_id"]
    api.post_cancel(booking["ref"])
    api.deliver_webhook(psp_payment_id, outcome="failed")
    current = api.get_booking(booking["ref"])
    assert current["state"] == "cancelled"
    assert current["refund"] is None
    assert current["payment"]["status"] == "failed"


def test_cancel_during_ticketing_is_refused_with_retry_after(api):
    api.configure_airline(mode="slow", count=1)
    booking = api.create_hold()
    api.post_payment(booking["ref"])
    api.wait_for_state(booking["ref"], "ticketing")
    response = api.post_cancel(booking["ref"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TICKETING_IN_PROGRESS"
    assert response.headers["Retry-After"] == "5"


def test_cancel_ticketed_refundable_fare_refund_arithmetic(api):
    booking = create_ticketed_booking(api, LAST_SEAT_FARE)  # refundable, cancel fee 15000
    assert api.post_cancel(booking["ref"]).status_code == 200
    refunded = api.wait_for_refund_succeeded(booking["ref"])
    # base 84251 (supplier "84250.50") - fee 15000 + PK 3200 + RG 6000; YQ 14300 retained
    assert refunded["refund"]["amount_pkr"] == 84251 - 15000 + 3200 + 6000
    assert refunded["refund"]["breakdown"]["per_passenger"]["non_refundable_taxes_pkr"] == 14300
    psp_refund_amounts = [refund["amount_pkr"] for refund in api.psp_state()["refunds"]]
    assert psp_refund_amounts == [78451]


def test_cancel_ticketed_non_refundable_fare_refunds_only_taxes_for_each_passenger(api):
    booking = create_ticketed_booking(api, "F-KHI-DXB-FZ334", passenger_count=2)  # non-refundable
    api.post_cancel(booking["ref"])
    refunded = api.wait_for_refund_succeeded(booking["ref"])
    assert refunded["refund"]["amount_pkr"] == (3200 + 5000) * 2
    assert refunded["refund"]["breakdown"]["rule"] == "non_refundable"


def test_ticketed_booking_is_refunded_once_even_if_cancelled_concurrently(api):
    booking = create_ticketed_booking(api, "F-LHE-KHI-PK302")
    with ThreadPoolExecutor(max_workers=5) as pool:
        status_codes = [response.status_code
                        for response in pool.map(lambda _: api.post_cancel(booking["ref"]), range(5))]
    assert status_codes == [200] * 5
    api.wait_for_refund_succeeded(booking["ref"])
    conn = api.open_db()
    try:
        refund_count = conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]
    finally:
        conn.close()
    assert refund_count == 1
    assert len(api.psp_state()["refunds"]) == 1
