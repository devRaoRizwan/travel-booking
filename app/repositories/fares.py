import sqlite3


def get(conn: sqlite3.Connection, fare_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM fares WHERE id = ?", (fare_id,)).fetchone()


def list_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM fares ORDER BY id").fetchall()


def taxes(conn: sqlite3.Connection, fare_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT code, name, amount_pkr, refundable FROM fare_taxes WHERE fare_id = ? ORDER BY code", (fare_id,)
    ).fetchall()


# Atomic conditional decrement; False when not enough seats.
def try_reserve_seats(conn: sqlite3.Connection, fare_id: str, seat_count: int) -> bool:
    cursor = conn.execute(
        "UPDATE fares SET seats_left = seats_left - ? WHERE id = ? AND seats_left >= ?",
        (seat_count, fare_id, seat_count),
    )
    return cursor.rowcount == 1


def release_seats(conn: sqlite3.Connection, fare_id: str, seat_count: int) -> None:
    conn.execute("UPDATE fares SET seats_left = seats_left + ? WHERE id = ?", (seat_count, fare_id))
