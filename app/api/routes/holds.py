from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_container, get_db, get_principal, to_response
from app.services import holds, idempotency
from app.services.commands import HoldRequest

router = APIRouter(prefix="/v1/holds", tags=["holds"])


@router.post("", status_code=201)
def create_hold(hold_request: HoldRequest, conn=Depends(get_db), principal=Depends(get_principal),
                container=Depends(get_container), idempotency_key: str | None = Header(None)):
    validated_key = idempotency.validate_key(idempotency_key, required=False)
    return to_response(holds.create_hold(conn, container.settings, principal, hold_request, validated_key))
