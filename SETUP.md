# API usage

Start the service:

```bash
python3 entrypoint.py
```

Copy the partner key printed at startup:

```bash
export KEY=tpk_xxxxxxxx_xxxxxxxx
export API=http://127.0.0.1:8000
```

Every call below sends `Authorization: Bearer $KEY` unless stated otherwise. Interactive docs are at `$API/docs`.

Errors always have this shape:

```json
{
  "error": {
    "code": "SOLD_OUT",
    "message": "fewer than 1 seat(s) left on this fare"
  }
}
```

## 1. List fares

```bash
curl -s $API/v1/fares -H "Authorization: Bearer $KEY"
```

`200`. One entry, shown:

```json
{
  "fares": [
    {
      "id": "F-ISB-JED-SV723",
      "carrier": "SV",
      "flight_no": "SV723",
      "origin": "ISB",
      "destination": "JED",
      "depart_at": "2026-12-02T03:05:00+05:00",
      "cabin": "Y",
      "fare_basis": "YSAVPK",
      "baggage": "2x23kg",
      "seats_left": 1,
      "international": true,
      "base_fare_pkr": 84251,
      "taxes": [
        {
          "code": "PK",
          "name": "Pakistan CAA departure charge",
          "amount_pkr": 3200,
          "refundable": true
        },
        {
          "code": "RG",
          "name": "Pakistan advance income tax",
          "amount_pkr": 6000,
          "refundable": true
        },
        {
          "code": "YQ",
          "name": "Carrier fuel surcharge",
          "amount_pkr": 14300,
          "refundable": false
        }
      ],
      "total_pkr": 107751,
      "rules": {
        "refundable": true,
        "cancel_fee_pkr": 15000,
        "change_fee_pkr": 7500
      }
    }
  ]
}
```

## 2. Create a hold

Reserves seats for 20 minutes and copies the price onto the booking. `Idempotency-Key` is optional.

```bash
curl -s -X POST $API/v1/holds \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
  "fare_id": "F-ISB-JED-SV723",
  "passengers": [
    {
      "title": "MS",
      "given_name": "Ayesha",
      "surname": "Malik",
      "gender": "F",
      "date_of_birth": "1992-03-04",
      "nationality": "PK",
      "document": {
        "type": "passport",
        "number": "BX7654321",
        "issuing_country": "PK",
        "expires_on": "2031-05-01"
      }
    }
  ],
  "contact": {
    "email": "ayesha@example.com",
    "phone": "+923211234567"
  },
  "consent": {
    "terms_version": "tos-2.1",
    "fare_rules_accepted": true
  }
}'
```

Field rules:

- `title`: `MR`, `MRS`, `MS`, `MSTR` or `MISS`.
- `gender`: `M`, `F` or `X`.
- Country fields are 2-letter ISO codes.
- `phone` is E.164.
- `passengers` holds 1 to 9 entries.
- International fares need a passport valid 6 months past departure.
- `fare_rules_accepted` must be `true`.

`201`. This is the booking object returned by every booking endpoint:

```json
{
  "ref": "TPK-ZJQSNA",
  "state": "held",
  "fare": {
    "id": "F-ISB-JED-SV723",
    "carrier": "SV",
    "flight_no": "SV723",
    "origin": "ISB",
    "destination": "JED",
    "depart_at": "2026-12-02T03:05:00+05:00",
    "cabin": "Y",
    "fare_basis": "YSAVPK",
    "baggage": "2x23kg"
  },
  "fare_rules": {
    "refundable": true,
    "cancel_fee_pkr": 15000
  },
  "consent": {
    "terms_version": "tos-2.1",
    "fare_rules_accepted": true,
    "accepted_at": "2026-10-08T09:03:01.916Z"
  },
  "passengers": [
    {
      "title": "MS",
      "given_name": "Ayesha",
      "surname": "Malik"
    }
  ],
  "price": {
    "currency": "PKR",
    "per_passenger": [
      {
        "kind": "base",
        "code": "BASE",
        "name": "Base fare",
        "amount_pkr": 84251,
        "refundable": true
      },
      {
        "kind": "tax",
        "code": "PK",
        "name": "Pakistan CAA departure charge",
        "amount_pkr": 3200,
        "refundable": true
      },
      {
        "kind": "tax",
        "code": "RG",
        "name": "Pakistan advance income tax",
        "amount_pkr": 6000,
        "refundable": true
      },
      {
        "kind": "tax",
        "code": "YQ",
        "name": "Carrier fuel surcharge",
        "amount_pkr": 14300,
        "refundable": false
      }
    ],
    "per_passenger_total_pkr": 107751,
    "passengers": 1,
    "total_pkr": 107751,
    "total_display": "PKR 107,751"
  },
  "hold_expires_at": "2026-10-08T09:23:01.916Z",
  "payment": null,
  "ticket": null,
  "ticketing": {
    "attempts": 0,
    "last_error": null
  },
  "refund": null,
  "created_at": "2026-10-08T09:03:01.916Z",
  "updated_at": "2026-10-08T09:03:01.916Z"
}
```

Partners also receive an `events` array (the state history). Customers do not.

```bash
export REF=TPK-ZJQSNA
```

Errors:

| Status | Code | When |
|---|---|---|
| 409 | `SOLD_OUT` | not enough seats |
| 404 | `FARE_NOT_FOUND` | unknown `fare_id` |
| 422 | `DOCUMENT_REQUIRED` | CNIC on an international fare |
| 422 | `DOCUMENT_EXPIRES_TOO_SOON` | passport not valid 6 months past departure |
| 422 | `VALIDATION_ERROR` | bad or missing field |

## 3. Get a booking

```bash
curl -s $API/v1/bookings/$REF -H "Authorization: Bearer $KEY"
```

`200` with the booking object. A ref that doesn't exist, or belongs to another partner or customer, returns the same response:

```json
{
  "error": {
    "code": "BOOKING_NOT_FOUND",
    "message": "booking not found"
  }
}
```

## 4. Issue a customer token

Gives the customer a 24-hour token that can read, pay for and cancel this one booking.

```bash
curl -s -X POST $API/v1/bookings/$REF/customer-token -H "Authorization: Bearer $KEY"
```

`201`:

```json
{
  "token": "ctk_OrQw1LylkzJdbnnPuahosgN4x0z-dkH2-9iDS2kiDaI",
  "booking_ref": "TPK-ZJQSNA",
  "expires_at": "2026-10-09T09:03:01.940Z"
}
```

Use it the same way: `Authorization: Bearer ctk_...`.

## 5. Start a payment

`Idempotency-Key` is required. `method` is one of `jazzcash`, `easypaisa`, `raast` or `card`. The amount always comes from the booking, never from the request.

```bash
curl -s -X POST $API/v1/bookings/$REF/payments \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-7f3a9c21" \
  -d '{"method": "jazzcash"}'
```

`202`:

```json
{
  "payment_id": "pa_6418413a75fbcdf5",
  "booking_ref": "TPK-ZJQSNA",
  "status": "pending",
  "amount_pkr": 107751,
  "currency": "PKR",
  "method": "jazzcash",
  "psp_payment_id": "pay_6a3ffa34f86efd70"
}
```

The PSP stub sends the result by webhook 0 to 2 seconds later. The booking then moves to `ticketing`, and then to `ticketed`:

```json
{
  "ref": "TPK-ZJQSNA",
  "state": "ticketed",
  "payment": {
    "id": "pa_6418413a75fbcdf5",
    "status": "succeeded",
    "method": "jazzcash",
    "amount_pkr": 107751
  },
  "ticket": {
    "pnr": "ZBCU99"
  },
  "ticketing": {
    "attempts": 1,
    "last_error": null
  }
}
```

Retrying:

- Same key and body: the original `202` is returned with header `Idempotent-Replayed: true`. Nothing is charged again.
- Same key, different body:

```json
{
  "error": {
    "code": "IDEMPOTENCY_KEY_REUSED",
    "message": "this Idempotency-Key was already used with a different request"
  }
}
```

Errors:

| Status | Code | When |
|---|---|---|
| 400 | `IDEMPOTENCY_KEY_REQUIRED` | header missing |
| 409 | `HOLD_EXPIRED` | hold passed its 20 minutes |
| 409 | `PAYMENT_IN_PROGRESS` | another payment is pending |
| 409 | `INVALID_STATE` | booking already paid or cancelled |
| 422 | `IDEMPOTENCY_KEY_REUSED` | key used with a different request |
| 502 | `PSP_UNAVAILABLE` | PSP down; retry with the same key |

## 6. Cancel

```bash
curl -s -X POST $API/v1/bookings/$REF/cancel -H "Authorization: Bearer $KEY"
```

`200` with the booking object. What happens depends on the state:

| State | Result |
|---|---|
| `held` | cancelled, seat released, no refund |
| `payment_pending` | cancelled, seat released; refunded in full if the payment later succeeds |
| `ticketed` | cancelled, refund per fare rules |
| `ticketing` | `409 TICKETING_IN_PROGRESS` with `Retry-After: 5` |
| `cancelled` | no change |

Ticketed SV723 example. The refund starts `pending` and becomes `succeeded` once the PSP accepts it:

```json
{
  "state": "cancelled",
  "refund": {
    "id": "rf_2eeee8e781d17c4b",
    "status": "succeeded",
    "reason": "customer_cancellation",
    "amount_pkr": 78451,
    "amount_display": "PKR 78,451",
    "breakdown": {
      "per_passenger": {
        "base_fare_pkr": 84251,
        "cancel_fee_pkr": 15000,
        "base_fare_refund_pkr": 69251,
        "refundable_taxes_pkr": 9200,
        "non_refundable_taxes_pkr": 14300,
        "refund_pkr": 78451
      },
      "passengers": 1,
      "rule": "refundable"
    }
  }
}
```

## 7. PSP webhook

The PSP stub calls this endpoint; clients don't. It is authenticated by HMAC, not a bearer token.

```
POST /v1/webhooks/psp
PSP-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + raw body)>
```

```json
{
  "id": "evt_1f2e3d4c5b6a7980",
  "type": "payment.succeeded",
  "created": 1791450182,
  "data": {
    "payment_id": "pay_6a3ffa34f86efd70",
    "merchant_reference": "pa_6418413a75fbcdf5",
    "amount_pkr": 107751,
    "currency": "PKR"
  }
}
```

Responses:

| Status | When |
|---|---|
| 200 | accepted; body `{"received": true, "outcome": "ticketing"}` |
| 200 | duplicate event id; outcome `duplicate_event`, nothing applied |
| 401 | bad signature, or timestamp more than 300 s old |
| 404 | unknown `merchant_reference`; the PSP retries |

## 8. Triggering failure paths

The stubs have control endpoints for reproducing failures by hand.

Airline (`:8002`). `count` is how many requests the mode applies to:

```bash
curl -s -X POST http://127.0.0.1:8002/_control -H "Content-Type: application/json" -d '{"mode": "slow", "count": 1}'
```

Modes:

- `slow`: answers after 6 s, past our 3 s timeout, then is retried.
- `flaky`: returns 503.
- `error_after_issue`: issues the ticket, then returns 500.
- `reject`: returns 409, which triggers a full refund.

PSP (`:8001`):

```bash
# hold webhooks instead of sending them
curl -s -X POST http://127.0.0.1:8001/_control -H "Content-Type: application/json" -d '{"auto_deliver": false}'

# deliver one later, e.g. after the hold has expired
curl -s -X POST http://127.0.0.1:8001/_control/payments/<psp_payment_id>/deliver \
  -H "Content-Type: application/json" -d '{"outcome": "succeeded"}'
```

Delivery options: `outcome` (`succeeded` or `failed`), `times` (duplicate deliveries), `timestamp_offset_s`, `bad_signature`, `amount_pkr`.
