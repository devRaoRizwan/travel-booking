# PSP stub: 202, then a signed webhook after delay_ms. Refunds are synchronous. /_control steers it in tests.

import hashlib
import hmac
import json
import random
import secrets
import threading
import time

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

DEFAULT_CONFIG = {
    "auto_deliver": True,     # False: webhooks sent only via /_control/payments/{id}/deliver
    "outcome": "succeeded",   # "succeeded" | "failed"
    "delay_ms": [0, 2000],    # uniform random delay range before delivery
    "times": 1,               # deliveries per event, same event id (2 = duplicate delivery)
    "fail_create": 0,         # next N POST /payments return 503
}
DELIVERY_ATTEMPTS = 3
DELIVERY_RETRY_DELAY_S = 0.3


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def create_psp_app(webhook_secret: str) -> FastAPI:
    app = FastAPI(title="PSP stub")
    lock = threading.Lock()
    state = {"config": dict(DEFAULT_CONFIG), "payments": {}, "payments_by_key": {}, "refunds_by_key": {},
             "deliveries": []}

    def deliver(payment: dict, outcome: str, times: int = 1, timestamp_offset_s: int = 0,
                bad_signature: bool = False, amount_pkr: int | None = None) -> str:
        event = {
            "id": "evt_" + secrets.token_hex(8),
            "type": f"payment.{outcome}",
            "created": int(time.time()),
            "data": {
                "payment_id": payment["payment_id"],
                "merchant_reference": payment["merchant_reference"],
                "amount_pkr": payment["amount_pkr"] if amount_pkr is None else amount_pkr,
                "currency": payment["currency"],
            },
        }
        event_body = json.dumps(event).encode()
        with lock:  # status set before notifying the merchant
            payment["status"] = outcome
        signing_secret = "wrong-secret" if bad_signature else webhook_secret
        for _ in range(times):
            signature_header = sign(signing_secret, event_body, int(time.time()) + timestamp_offset_s)
            delivery_status = None
            for _ in range(DELIVERY_ATTEMPTS):
                try:
                    response = httpx.post(payment["webhook_url"], content=event_body, timeout=5,
                                          headers={"PSP-Signature": signature_header,
                                                   "Content-Type": "application/json"})
                    delivery_status = response.status_code
                    if response.status_code < 300 or response.status_code in (400, 401):
                        break
                except httpx.HTTPError:
                    delivery_status = "error"
                time.sleep(DELIVERY_RETRY_DELAY_S)
            with lock:
                state["deliveries"].append({"event_id": event["id"], "payment_id": payment["payment_id"],
                                            "type": event["type"], "status": delivery_status})
        return event["id"]

    @app.post("/payments", status_code=202)
    def create_payment(payload: dict, idempotency_key: str | None = Header(None)):
        if not idempotency_key:
            return JSONResponse({"error": "Idempotency-Key required"}, 400)
        with lock:
            config = state["config"]
            if config["fail_create"] > 0:
                config["fail_create"] -= 1
                return JSONResponse({"error": "temporarily unavailable"}, 503)
            existing_payment = state["payments_by_key"].get(idempotency_key)
            if existing_payment:
                return {"payment_id": existing_payment["payment_id"], "status": "accepted"}
            if not isinstance(payload.get("amount_pkr"), int) or payload["amount_pkr"] <= 0:
                return JSONResponse({"error": "amount_pkr must be a positive integer"}, 422)
            payment = {
                "payment_id": "pay_" + secrets.token_hex(8),
                "merchant_reference": payload["merchant_reference"],
                "amount_pkr": payload["amount_pkr"],
                "currency": payload.get("currency", "PKR"),
                "webhook_url": payload["webhook_url"],
                "status": "pending",
                "refunded_pkr": 0,
            }
            state["payments"][payment["payment_id"]] = payment
            state["payments_by_key"][idempotency_key] = payment
            auto_deliver, outcome, times = config["auto_deliver"], config["outcome"], config["times"]
            min_delay_ms, max_delay_ms = config["delay_ms"]
        if auto_deliver:
            delay_s = random.uniform(min_delay_ms, max_delay_ms) / 1000
            threading.Timer(delay_s, deliver, args=(payment, outcome, times)).start()
        return {"payment_id": payment["payment_id"], "status": "accepted"}

    @app.post("/refunds")
    def create_refund(payload: dict, idempotency_key: str | None = Header(None)):
        if not idempotency_key:
            return JSONResponse({"error": "Idempotency-Key required"}, 400)
        with lock:
            if idempotency_key in state["refunds_by_key"]:
                return state["refunds_by_key"][idempotency_key]
            payment = state["payments"].get(payload.get("payment_id"))
            if payment is None or payment["status"] != "succeeded":
                return JSONResponse({"error": "payment not captured"}, 409)
            amount_pkr = payload.get("amount_pkr")
            if (not isinstance(amount_pkr, int) or amount_pkr <= 0
                    or payment["refunded_pkr"] + amount_pkr > payment["amount_pkr"]):
                return JSONResponse({"error": "invalid refund amount"}, 422)
            payment["refunded_pkr"] += amount_pkr
            refund = {"refund_id": "re_" + secrets.token_hex(8), "payment_id": payment["payment_id"],
                      "amount_pkr": amount_pkr, "status": "succeeded"}
            state["refunds_by_key"][idempotency_key] = refund
            return refund

    @app.post("/_control")
    def configure(config_update: dict):
        with lock:
            state["config"].update(config_update)
            return state["config"]

    @app.post("/_control/reset")
    def reset():
        with lock:
            state.update(config=dict(DEFAULT_CONFIG), payments={}, payments_by_key={}, refunds_by_key={},
                         deliveries=[])
        return {"ok": True}

    @app.get("/_control/payments")
    def list_payments():
        with lock:
            return {"payments": list(state["payments"].values()),
                    "refunds": list(state["refunds_by_key"].values()),
                    "deliveries": list(state["deliveries"])}

    @app.post("/_control/payments/{payment_id}/deliver")
    def deliver_now(payment_id: str, options: dict | None = None):
        options = options or {}
        payment = state["payments"].get(payment_id)
        if payment is None:
            return JSONResponse({"error": "unknown payment"}, 404)
        event_id = deliver(payment, options.get("outcome", "succeeded"), options.get("times", 1),
                           options.get("timestamp_offset_s", 0), options.get("bad_signature", False),
                           options.get("amount_pkr"))
        return {"event_id": event_id,
                "deliveries": [delivery for delivery in state["deliveries"] if delivery["event_id"] == event_id]}

    return app
