import json
import sqlite3
from pathlib import Path

from app.domain.money import parse_pkr
from app.infrastructure.database import write_tx

FARES_FIXTURE = Path(__file__).resolve().parents[2] / "seed" / "fares.json"


# INSERT OR IGNORE, so a restart doesn't reset seats_left.
def seed_fares(conn: sqlite3.Connection, fixture_path: Path = FARES_FIXTURE) -> int:
    fixture = json.loads(fixture_path.read_text())
    inserted_count = 0
    with write_tx(conn):
        for fare in fixture["fares"]:
            rules = fare["rules"]
            cursor = conn.execute(
                """INSERT OR IGNORE INTO fares (id, carrier, flight_no, origin, destination, depart_at,
                       cabin, fare_basis, international, base_fare_pkr, seats_left, refundable,
                       cancel_fee_pkr, change_fee_pkr, baggage)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fare["id"], fare["carrier"], fare["flight_no"], fare["origin"], fare["destination"],
                    fare["depart_at"], fare["cabin"], fare["fare_basis"], int(fare["international"]),
                    parse_pkr(fare["base_fare"]), fare["seats_left"], int(rules["refundable"]),
                    parse_pkr(rules["cancel_fee"]),
                    parse_pkr(rules["change_fee"]) if rules["change_fee"] is not None else None,
                    rules["baggage"],
                ),
            )
            if cursor.rowcount == 0:
                continue
            inserted_count += 1
            for tax in fare["taxes"]:
                conn.execute(
                    "INSERT INTO fare_taxes (fare_id, code, name, amount_pkr, refundable) VALUES (?,?,?,?,?)",
                    (fare["id"], tax["code"], tax["name"], parse_pkr(tax["amount"]), int(tax["refundable"])),
                )
    return inserted_count
