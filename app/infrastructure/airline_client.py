from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass
class IssueResult:
    outcome: str  # 'issued' | 'retryable' (timeout/5xx/429, outcome unknown) | 'rejected' (4xx)
    pnr: str | None = None
    error: str | None = None


class AirlineClient:
    def __init__(self, settings: Settings):
        self._http = httpx.Client(base_url=settings.airline_url, timeout=settings.airline_timeout_s)

    # Idempotency-Key is constant per booking, so retries can't create a second PNR.
    def issue(self, booking_ref: str, fare_id: str, passengers: list[dict]) -> IssueResult:
        try:
            response = self._http.post(
                "/pnrs",
                json={"booking_ref": booking_ref, "fare_id": fare_id, "passengers": passengers},
                headers={"Idempotency-Key": f"tkt-{booking_ref}"},
            )
        except httpx.TimeoutException:
            return IssueResult("retryable", error="airline timeout")
        except httpx.HTTPError as error:
            return IssueResult("retryable", error=f"airline transport error: {type(error).__name__}")
        if response.status_code in (200, 201):
            return IssueResult("issued", pnr=response.json()["pnr"])
        if response.status_code >= 500 or response.status_code == 429:
            return IssueResult("retryable", error=f"airline {response.status_code}")
        return IssueResult("rejected", error=f"airline {response.status_code}: {response.text[:200]}")
