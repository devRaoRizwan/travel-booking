from dataclasses import dataclass, field

from .errors import Forbidden

ALL_SCOPES = frozenset({
    "fares:read", "holds:write", "bookings:read", "bookings:write",
    "payments:write", "bookings:cancel",
})
CUSTOMER_SCOPES = frozenset({"bookings:read", "payments:write", "bookings:cancel"})


# Authenticated caller: partner (scoped key) or customer (token bound to one booking).
@dataclass(frozen=True)
class Principal:
    kind: str                      # 'partner' | 'customer'
    partner_id: str
    scopes: frozenset = field(default_factory=frozenset)
    booking_id: int | None = None  # customer only

    @property
    def is_partner(self) -> bool:
        return self.kind == "partner"

    # Separate Idempotency-Key namespace per partner and per customer booking.
    @property
    def idempotency_scope(self) -> str:
        return f"partner:{self.partner_id}" if self.is_partner else f"customer:{self.booking_id}"

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise Forbidden("INSUFFICIENT_SCOPE", f"credential lacks scope {scope!r}")

    def can_see(self, booking) -> bool:
        if self.is_partner:
            return booking["partner_id"] == self.partner_id
        return booking["id"] == self.booking_id
