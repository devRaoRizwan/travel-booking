import sqlite3

from app.domain.principal import Principal
from app.repositories import fares

FARE_FIELDS = ("id", "carrier", "flight_no", "origin", "destination", "depart_at", "cabin", "fare_basis",
               "baggage", "seats_left")


def list_fares(conn: sqlite3.Connection, principal: Principal) -> dict:
    principal.require("fares:read")
    fare_views = []
    for fare in fares.list_all(conn):
        fare_taxes = fares.taxes(conn, fare["id"])
        fare_views.append({
            **{field: fare[field] for field in FARE_FIELDS},
            "international": bool(fare["international"]),
            "base_fare_pkr": fare["base_fare_pkr"],
            "taxes": [dict(tax) | {"refundable": bool(tax["refundable"])} for tax in fare_taxes],
            "total_pkr": fare["base_fare_pkr"] + sum(tax["amount_pkr"] for tax in fare_taxes),
            "rules": {
                "refundable": bool(fare["refundable"]),
                "cancel_fee_pkr": fare["cancel_fee_pkr"],
                "change_fee_pkr": fare["change_fee_pkr"],
            },
        })
    return {"fares": fare_views}
