# Per passenger: refundable = base - min(cancel_fee, base) + refundable taxes; non-refundable = refundable taxes.


def compute_refund(price_lines: list, passenger_count: int, fare_refundable: bool,
                   cancel_fee_pkr: int) -> tuple[int, dict]:
    base_fare = sum(line["amount_pkr"] for line in price_lines if line["kind"] == "base")
    refundable_taxes = sum(line["amount_pkr"] for line in price_lines
                           if line["kind"] == "tax" and line["refundable"])
    retained_taxes = sum(line["amount_pkr"] for line in price_lines
                         if line["kind"] == "tax" and not line["refundable"])
    if fare_refundable:
        cancel_fee = min(cancel_fee_pkr, base_fare)
        base_fare_refund = base_fare - cancel_fee
    else:
        cancel_fee = 0
        base_fare_refund = 0
    refund_per_passenger = base_fare_refund + refundable_taxes
    breakdown = {
        "per_passenger": {
            "base_fare_pkr": base_fare,
            "cancel_fee_pkr": cancel_fee,
            "base_fare_refund_pkr": base_fare_refund,
            "refundable_taxes_pkr": refundable_taxes,
            "non_refundable_taxes_pkr": retained_taxes,
            "refund_pkr": refund_per_passenger,
        },
        "passengers": passenger_count,
        "rule": "refundable" if fare_refundable else "non_refundable",
    }
    return refund_per_passenger * passenger_count, breakdown


def full_refund(reason: str) -> dict:
    return {"rule": "full_refund", "why": reason}
