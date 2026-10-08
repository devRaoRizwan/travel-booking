from fastapi import APIRouter, Depends, Header

from app.api.dependencies import get_container, get_db, get_principal, to_response
from app.services import bookings, cancellation, idempotency, payments
from app.services.commands import PaymentRequest

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])


@router.get("/{ref}")
def get_booking(ref: str, conn=Depends(get_db), principal=Depends(get_principal)):
    return bookings.read_booking(conn, principal, ref)


@router.post("/{ref}/customer-token", status_code=201)
def mint_customer_token(ref: str, conn=Depends(get_db), principal=Depends(get_principal),
                        container=Depends(get_container)):
    return bookings.mint_customer_token(conn, container.settings, principal, ref)


@router.post("/{ref}/payments", status_code=202)
def start_payment(ref: str, payment_request: PaymentRequest, conn=Depends(get_db),
                  principal=Depends(get_principal), container=Depends(get_container),
                  idempotency_key: str | None = Header(None)):
    validated_key = idempotency.validate_key(idempotency_key, required=True)
    return to_response(payments.start_payment(conn, container.settings, container.psp, principal, ref,
                                              payment_request, validated_key))


@router.post("/{ref}/cancel")
def cancel_booking(ref: str, conn=Depends(get_db), principal=Depends(get_principal),
                   container=Depends(get_container)):
    booking = cancellation.cancel_booking(conn, principal, ref)
    container.worker.kick()
    return booking
