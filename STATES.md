# Booking states

`held`, `payment_pending`, `ticketing`, `ticketed`, `expired`, `cancelled`, `ticketing_failed`, `needs_review`

Each transition runs in a `BEGIN IMMEDIATE` transaction, is guarded by the current state, and is logged to `booking_events`.

| From | Event | To | Side effects |
|---|---|---|---|
| – | create hold, seats available | held | seats decremented, price snapshot, expires in 20 min |
| – | create hold, not enough seats | – | 409 `SOLD_OUT` |
| held | start payment | payment_pending | attempt committed before PSP call; PSP key = attempt id |
| any | same Idempotency-Key, same body | unchanged | stored response returned |
| any | same Idempotency-Key, different body | unchanged | 422 |
| payment_pending | start payment, new key | unchanged | 409 `PAYMENT_IN_PROGRESS` |
| payment_pending | PSP timeout/5xx on start | unchanged | 502; retry with same key resubmits same attempt |
| payment_pending | PSP 4xx on start | held | attempt failed |
| held, payment_pending | hold expires | expired | seats released |
| expired | start payment | unchanged | 409 `HOLD_EXPIRED` |
| payment_pending | webhook succeeded | ticketing | attempt succeeded |
| payment_pending | webhook failed | held | attempt failed |
| expired | webhook succeeded | expired | full refund |
| cancelled | webhook succeeded | cancelled | full refund |
| expired, cancelled | webhook failed | unchanged | attempt failed |
| any | webhook, duplicate event id | unchanged | ignored |
| any | webhook, attempt already final | unchanged | ignored |
| – | webhook, bad signature / stale timestamp | – | 401 |
| any unpaid | webhook succeeded, wrong amount | needs_review | no ticketing |
| ticketing | airline 201 | ticketed | PNR stored |
| ticketing | airline timeout/5xx, attempts < 5 | ticketing | retry with backoff, same airline key |
| ticketing | airline timeout/5xx, attempts = 5 | needs_review | no refund (ticket may exist) |
| ticketing | airline 4xx | ticketing_failed | full refund |
| ticketing | worker dies mid-call | ticketing | lease expires, retried with same key |
| held | cancel | cancelled | seats released |
| payment_pending | cancel | cancelled | seats released; refund if payment later succeeds |
| ticketing | cancel | unchanged | 409, `Retry-After: 5` |
| ticketed | cancel | cancelled | refund per fare rules |
| cancelled | cancel | unchanged | no-op |
| expired, ticketing_failed, needs_review | cancel | unchanged | 409 `NOT_CANCELLABLE` |

At most one refund per captured payment (unique constraint). Refunds stay pending and retry until the PSP accepts.

## Refund on cancel (per passenger, PKR)

```
refundable:      base - min(cancel_fee, base) + refundable taxes
non-refundable:  refundable taxes
```

YQ (carrier surcharge) is never refunded.
Example, SV723: 84,251 - 15,000 + 3,200 + 6,000 = 78,451.
