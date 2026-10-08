from fastapi import APIRouter, Depends

from app.api.dependencies import get_db, get_principal
from app.services import fares

router = APIRouter(prefix="/v1/fares", tags=["fares"])


@router.get("")
def list_fares(conn=Depends(get_db), principal=Depends(get_principal)):
    return fares.list_fares(conn, principal)
