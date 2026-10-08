import httpx

from app.config import Settings


# Timeout or 5xx: outcome unknown, retry with the same key.
class PspUnavailable(Exception):
    pass


# 4xx: the PSP did not act.
class PspRejected(Exception):
    pass


class PspClient:
    def __init__(self, settings: Settings):
        self._http = httpx.Client(base_url=settings.psp_url, timeout=settings.psp_timeout_s)

    # Attempt id is the PSP idempotency key, so a resubmit can't double-charge.
    def create_payment(self, attempt_id: str, amount_pkr: int, method: str, webhook_url: str) -> dict:
        return self._post("/payments", attempt_id, {
            "merchant_reference": attempt_id, "amount_pkr": amount_pkr,
            "currency": "PKR", "method": method, "webhook_url": webhook_url,
        })

    def refund(self, refund_id: str, psp_payment_id: str, amount_pkr: int) -> dict:
        return self._post("/refunds", refund_id, {"payment_id": psp_payment_id, "amount_pkr": amount_pkr})

    def _post(self, path: str, idempotency_key: str, payload: dict) -> dict:
        try:
            response = self._http.post(path, json=payload, headers={"Idempotency-Key": idempotency_key})
        except httpx.HTTPError as error:
            raise PspUnavailable(f"{type(error).__name__}: {error}") from error
        if response.status_code >= 500:
            raise PspUnavailable(f"PSP {response.status_code}")
        if response.status_code >= 400:
            raise PspRejected(f"PSP {response.status_code}: {response.text[:200]}")
        return response.json()
