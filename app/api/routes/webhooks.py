import json

from fastapi import APIRouter, Depends, Header, Request
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies import get_container
from app.domain.errors import BadRequest
from app.infrastructure.database import connect
from app.infrastructure.security import verify_psp_signature
from app.services.webhooks import apply_psp_event

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/psp")
async def psp_webhook(request: Request, psp_signature: str | None = Header(None),
                      container=Depends(get_container)):
    raw_body = await request.body()  # HMAC is computed over the exact bytes
    verify_psp_signature(container.settings.psp_webhook_secret, psp_signature, raw_body,
                         container.settings.webhook_tolerance_s)
    try:
        event = json.loads(raw_body)
        event["id"], event["type"], event["data"]["merchant_reference"]
    except (ValueError, KeyError, TypeError):
        raise BadRequest("MALFORMED_EVENT", "unparseable webhook body")

    def apply_event() -> str:
        conn = connect(container.settings.db_path)
        try:
            return apply_psp_event(conn, event)
        finally:
            conn.close()

    outcome = await run_in_threadpool(apply_event)
    container.worker.kick()
    return {"received": True, "outcome": outcome}
