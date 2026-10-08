# Money is integer PKR. Supplier strings ("84250.50") are parsed strictly and rounded half-up.

import re

PKR_AMOUNT_PATTERN = re.compile(r"^(0|[1-9]\d{0,11})(?:\.(\d{1,2}))?$")


class MoneyFormatError(ValueError):
    pass


def parse_pkr(amount: str) -> int:
    if not isinstance(amount, str):
        raise MoneyFormatError(f"supplier amount must be a string, got {type(amount).__name__}")
    match = PKR_AMOUNT_PATTERN.match(amount)
    if not match:
        raise MoneyFormatError(f"invalid PKR amount: {amount!r}")
    rupees = int(match.group(1))
    hundredths = int((match.group(2) or "0").ljust(2, "0"))
    return rupees + (1 if hundredths >= 50 else 0)


def format_pkr(amount_pkr: int) -> str:
    sign = "-" if amount_pkr < 0 else ""
    return f"{sign}PKR {abs(amount_pkr):,}"
