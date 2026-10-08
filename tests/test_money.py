import pytest

from app.domain.money import MoneyFormatError, format_pkr, parse_pkr
from app.domain.refunds import compute_refund


@pytest.mark.parametrize("supplier_amount,expected_pkr", [
    ("84250", 84250),
    ("84250.00", 84250),
    ("84250.49", 84250),
    ("84250.50", 84251),
    ("84250.5", 84251),
    ("0.49", 0),
    ("0", 0),
])
def test_parse_supplier_string_to_integer_pkr_rounding_half_up(supplier_amount, expected_pkr):
    parsed = parse_pkr(supplier_amount)
    assert parsed == expected_pkr
    assert type(parsed) is int


@pytest.mark.parametrize("supplier_amount", ["84250.505", "-1", "1e5", " 100", "84,250.50", "", ".5", "01", "NaN"])
def test_parse_rejects_ambiguous_or_lossy_input(supplier_amount):
    with pytest.raises(MoneyFormatError):
        parse_pkr(supplier_amount)


def test_parse_rejects_float_input():
    with pytest.raises(MoneyFormatError):
        parse_pkr(84250.50)


def test_format_is_display_only():
    assert format_pkr(84251) == "PKR 84,251"
    assert format_pkr(5) == "PKR 5"


SV723_PRICE_LINES = [  # supplier base "84250.50" -> 84251
    {"kind": "base", "code": "BASE", "amount_pkr": 84251, "refundable": 1},
    {"kind": "tax", "code": "PK", "amount_pkr": 3200, "refundable": 1},
    {"kind": "tax", "code": "RG", "amount_pkr": 6000, "refundable": 1},
    {"kind": "tax", "code": "YQ", "amount_pkr": 14300, "refundable": 0},
]


def test_refund_refundable_fare_keeps_cancel_fee_and_carrier_surcharge():
    refund_amount, breakdown = compute_refund(SV723_PRICE_LINES, passenger_count=2, fare_refundable=True,
                                              cancel_fee_pkr=15000)
    expected_per_passenger = (84251 - 15000) + 3200 + 6000
    assert breakdown["per_passenger"]["refund_pkr"] == expected_per_passenger
    assert refund_amount == expected_per_passenger * 2


def test_refund_non_refundable_fare_returns_only_refundable_taxes():
    refund_amount, breakdown = compute_refund(SV723_PRICE_LINES, passenger_count=1, fare_refundable=False,
                                              cancel_fee_pkr=0)
    assert refund_amount == 3200 + 6000
    assert breakdown["per_passenger"]["base_fare_refund_pkr"] == 0


def test_refund_cancel_fee_larger_than_base_never_goes_negative():
    refund_amount, _ = compute_refund(SV723_PRICE_LINES, passenger_count=1, fare_refundable=True,
                                      cancel_fee_pkr=999_999)
    assert refund_amount == 3200 + 6000


def test_money_columns_are_stored_as_sqlite_integers(api):
    api.create_hold("F-ISB-JED-SV723")
    conn = api.open_db()
    try:
        column_types = conn.execute(
            "SELECT typeof(base_fare_pkr) FROM fares UNION SELECT typeof(amount_pkr) FROM fare_taxes "
            "UNION SELECT typeof(total_pkr) FROM bookings UNION SELECT typeof(amount_pkr) FROM booking_price_lines"
        ).fetchall()
    finally:
        conn.close()
    assert {row[0] for row in column_types} == {"integer"}
