# Travel.pk booking API

Hold → payment → ticket, with stub PSP and airline. Python 3.11+, FastAPI, SQLite, httpx, pytest.

## Run

```
python3 entrypoint.py          # create .venv if missing, install requirements, start everything
python3 entrypoint.py --test   # same setup, run the tests
```

- API: http://127.0.0.1:8000. OpenAPI docs: `/docs` (Swagger), `/redoc`.
- PSP stub: :8001. Airline stub: :8002.
- A demo partner key is printed at startup. Paste it into `/docs` → Authorize.
- Delete `travel.db` to reset seats.

## Layout

```
app/api             routes, auth dependency, error mapping
app/services        use cases; own the transactions
app/repositories    SQL
app/domain          states, money, refund rules, errors (no I/O)
app/infrastructure  db, PSP/airline clients, hashing, HMAC
app/workers         expiry, ticketing and refund loop
stubs/              PSP and airline stubs
seed/fares.json     6 fares; PA401 and FZ334 non-refundable; SV723 has 1 seat
```

## API

Request and response examples for every endpoint: SETUP.md.

Auth: `Authorization: Bearer <token>`.
- Partner key: `tpk_…`, scoped.
- Customer token: `ctk_…`, 24h, one booking.

Both are stored as SHA-256 hashes. Error body: `{"error": {"code": "...", "message": "..."}}`.

| Endpoint | Auth | Success | Errors |
|---|---|---|---|
| `GET /v1/fares` | `fares:read` | 200 | |
| `POST /v1/holds` | `holds:write`; `Idempotency-Key` optional | 201 booking | 404 `FARE_NOT_FOUND`, 409 `SOLD_OUT`, 422 validation / documents |
| `GET /v1/bookings/{ref}` | `bookings:read` or customer | 200 booking | 404 |
| `POST /v1/bookings/{ref}/customer-token` | `bookings:write` | 201 `{token, expires_at}` | 404 |
| `POST /v1/bookings/{ref}/payments` | `payments:write` or customer; `Idempotency-Key` required | 202 | 400 missing key, 409 `HOLD_EXPIRED` / `PAYMENT_IN_PROGRESS`, 422 `IDEMPOTENCY_KEY_REUSED`, 502 `PSP_UNAVAILABLE` |
| `POST /v1/bookings/{ref}/cancel` | `bookings:cancel` or customer | 200 booking | 409 `TICKETING_IN_PROGRESS`, `NOT_CANCELLABLE` |
| `POST /v1/webhooks/psp` | HMAC `PSP-Signature: t=…,v1=…` | 200 | 401, 404 unknown payment |

A booking that belongs to someone else returns the same 404 as a nonexistent one. A missing scope returns 403.

Money is integer PKR. Supplier strings such as `"84250.50"` are parsed with a regex and rounded half-up to whole rupees. Prices are copied onto the booking at hold time.

## Failure paths

Full table in STATES.md.

| Case | Behaviour |
|---|---|
| Two holds race for the last seat | `UPDATE … WHERE seats_left >= n` in `BEGIN IMMEDIATE`. One wins; the rest get 409. |
| Replayed Idempotency-Key | Stored response is returned. No new attempt, no new PSP call. |
| Same key, different body | 422. |
| PSP down when starting payment | Attempt is committed before the call. 502 is not stored. A retry with the same key resubmits the same attempt id. |
| Airline timeout | Treated as unknown. Retries with backoff reuse the same airline idempotency key. After 5 attempts the booking goes to `needs_review` with no refund. Airline 4xx gives a full refund. |
| Payment after hold expiry | Seat already released. Payment is refunded in full, no ticket. |
| Cancel during pending payment | Cancelled and seat released immediately. A later successful payment is refunded in full. |
| Cancel during ticketing | 409 with `Retry-After`. |
| Duplicate or late webhooks | Deduped by event id. The first terminal outcome wins. |
| Hold expiry | Background sweeper, plus a check before every operation on the booking. Seats are released once. |

## Trade-offs

- Single process, SQLite, in-process worker.
- No PSP reconciliation job. If every webhook for a captured payment is lost, nothing notices. This is the biggest gap.
- No airline void when a ticketed booking is cancelled.
- A late payment is always refunded, even if seats are still available.
- No TTL purge for idempotency keys or webhook events. No rate limiting.
- Rounding supplier prices per price line can move a multi-tax total by a rupee.

## 50 bookings/min on one VPS

1. **Ticketing pool.** 4 threads with a 3s airline timeout gives about 80 attempts/min when the airline is slow; worst case needs about 250. The backlog grows, and queued tasks can outlive their lease and be claimed twice. The idempotency key prevents duplicate PNRs, but bookings hit `needs_review` early. Fix: claim only up to free pool capacity and add a circuit breaker.
2. **GET takes the write lock** (for lazy expiry), so polling clients queue behind writes. Fix: read in a deferred transaction and lock only if expired.
3. **Unbounded event and key tables** grow by roughly 200k rows a day.

SQLite write throughput is not the limit at this rate.

## Tests

The API and both stubs run as real servers. Airline timeout and slow mode are scaled to 0.5s / 1.0s.

| Area | Status |
|---|---|
| Money parsing, integer columns | done |
| Hold: seats, price snapshot, ref, expiry | done |
| Last-seat race (10 concurrent) | done |
| Expiry via sweeper and on read | done |
| Idempotency: missing, replay, concurrent, different body, other booking | done |
| Webhooks: signature, stale timestamp, tamper, duplicate, out of order, amount mismatch | done |
| Airline timeout / 503 / 500-after-issue / reject / retries exhausted | done |
| Payment after expiry, cancel during payment, cancel during ticketing | done |
| Refund arithmetic, single refund under concurrent cancels | done |
| Auth: scopes, hashing, 404 rule, token expiry, revocation | done |
| Worker crash mid-ticketing (lease expiry) | partial: implemented, not tested |
| PSP refund failure retry | partial: implemented, not tested |
| Cancel after departure | partial: implemented, not tested |
| PSP reconciliation, multi-process, load | not done |
