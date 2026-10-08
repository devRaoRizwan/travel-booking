import json
import sqlite3

from app.domain.money import format_pkr
from app.infrastructure.clock import iso
from app.repositories import bookings, fares, payments, refunds

FARE_FIELDS = ("id", "carrier", "flight_no", "origin", "destination", "depart_at", "cabin", "fare_basis", "baggage")


# Passenger document numbers are never returned.
def render(conn: sqlite3.Connection, booking: sqlite3.Row, include_events: bool = False) -> dict:
    fare = fares.get(conn, booking["fare_id"])
    payment_attempt = payments.latest_for_booking(conn, booking["id"])
    refund = refunds.latest_for_booking(conn, booking["id"])
    view = {
        "ref": booking["ref"],
        "state": booking["state"],
        "fare": {field: fare[field] for field in FARE_FIELDS},
        "fare_rules": {"refundable": bool(booking["refundable"]), "cancel_fee_pkr": booking["cancel_fee_pkr"]},
        "consent": json.loads(booking["consent_json"]),
        "passengers": [
            {"title": passenger["title"], "given_name": passenger["given_name"], "surname": passenger["surname"]}
            for passenger in json.loads(booking["passengers_json"])
        ],
        "price": {
            "currency": "PKR",
            "per_passenger": [dict(line) | {"refundable": bool(line["refundable"])}
                              for line in bookings.price_lines(conn, booking["id"])],
            "per_passenger_total_pkr": booking["per_passenger_pkr"],
            "passengers": booking["pax_count"],
            "total_pkr": booking["total_pkr"],
            "total_display": format_pkr(booking["total_pkr"]),
        },
        "hold_expires_at": iso(booking["hold_expires_at"]),
        "payment": None if payment_attempt is None else {
            "id": payment_attempt["id"],
            "status": payment_attempt["status"],
            "method": payment_attempt["method"],
            "amount_pkr": payment_attempt["amount_pkr"],
        },
        "ticket": {"pnr": booking["pnr"]} if booking["pnr"] else None,
        "ticketing": {"attempts": booking["ticketing_attempts"], "last_error": booking["last_error"]},
        "refund": None if refund is None else {
            "id": refund["id"],
            "status": refund["status"],
            "reason": refund["reason"],
            "amount_pkr": refund["amount_pkr"],
            "amount_display": format_pkr(refund["amount_pkr"]),
            "breakdown": json.loads(refund["breakdown_json"]),
        },
        "created_at": iso(booking["created_at"]),
        "updated_at": iso(booking["updated_at"]),
    }
    if include_events:
        view["events"] = [
            {"from": event["from_state"], "to": event["to_state"], "event": event["event"],
             "detail": event["detail"], "at": iso(event["at"])}
            for event in bookings.events(conn, booking["id"])
        ]
    return view
