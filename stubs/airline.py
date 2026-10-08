# Airline stub. Dedup only with Idempotency-Key. Modes: normal, slow, flaky (503), error_after_issue (500 after issuing), reject (409).

import asyncio
import secrets
import string

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

DEFAULT_CONFIG = {"mode": "normal", "count": None, "slow_s": 6.0}
PNR_ALPHABET = string.ascii_uppercase + "23456789"
PNR_LENGTH = 6


def create_airline_app() -> FastAPI:
    app = FastAPI(title="Airline stub")
    state = {"config": dict(DEFAULT_CONFIG), "pnr_by_key": {}, "issued_pnrs": [], "call_count": 0}

    def consume_mode() -> str:
        config = state["config"]
        mode = config["mode"]
        if config["count"] is not None:
            config["count"] -= 1
            if config["count"] <= 0:
                config.update(mode="normal", count=None)
        return mode

    def generate_pnr() -> str:
        return "".join(secrets.choice(PNR_ALPHABET) for _ in range(PNR_LENGTH))

    # Single event loop and no await between read and write of state: no interleaving.
    @app.post("/pnrs", status_code=201)
    async def issue_pnr(payload: dict, idempotency_key: str | None = Header(None)):
        state["call_count"] += 1
        mode = consume_mode()
        if mode == "flaky":
            return JSONResponse({"error": "service unavailable"}, 503)
        if mode == "reject":
            return JSONResponse({"error": "fare class closed"}, 409)

        if idempotency_key and idempotency_key in state["pnr_by_key"]:
            pnr = state["pnr_by_key"][idempotency_key]
        else:
            pnr = generate_pnr()
            state["issued_pnrs"].append({"pnr": pnr, "booking_ref": payload.get("booking_ref"),
                                         "key": idempotency_key})
            if idempotency_key:
                state["pnr_by_key"][idempotency_key] = pnr

        if mode == "slow":
            await asyncio.sleep(state["config"]["slow_s"])
        if mode == "error_after_issue":
            return JSONResponse({"error": "internal error"}, 500)
        return {"pnr": pnr, "booking_ref": payload.get("booking_ref")}

    @app.post("/_control")
    async def configure(config_update: dict):
        state["config"].update(config_update)
        return state["config"]

    @app.post("/_control/reset")
    async def reset():
        state.update(config=dict(DEFAULT_CONFIG), pnr_by_key={}, issued_pnrs=[], call_count=0)
        return {"ok": True}

    @app.get("/_control/pnrs")
    async def list_pnrs():
        return {"pnrs": state["issued_pnrs"], "calls": state["call_count"]}

    return app
