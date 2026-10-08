HELD = "held"
PAYMENT_PENDING = "payment_pending"
TICKETING = "ticketing"
TICKETED = "ticketed"
EXPIRED = "expired"
CANCELLED = "cancelled"
TICKETING_FAILED = "ticketing_failed"  # airline returned 4xx; full refund issued
NEEDS_REVIEW = "needs_review"          # ticketing outcome unknown after max retries; manual reconciliation

# Unpaid states: transition to EXPIRED at hold_expires_at and release seats.
EXPIRABLE = (HELD, PAYMENT_PENDING)
