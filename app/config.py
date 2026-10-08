import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "travel.db"
    public_base_url: str = "http://127.0.0.1:8000"  # webhook URL base sent to the PSP
    psp_url: str = "http://127.0.0.1:8001"
    psp_webhook_secret: str = "dev-only-psp-secret"
    psp_timeout_s: float = 5.0
    airline_url: str = "http://127.0.0.1:8002"
    airline_timeout_s: float = 3.0
    hold_ttl_s: float = 20 * 60
    customer_token_ttl_s: float = 24 * 3600
    webhook_tolerance_s: float = 300
    ticketing_max_attempts: int = 5
    ticketing_backoff_s: float = 1.0  # base delay; doubles per attempt
    refund_retry_s: float = 5.0
    worker_interval_s: float = 0.5
    run_worker: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            db_path=os.getenv("TPK_DB_PATH", defaults.db_path),
            public_base_url=os.getenv("TPK_PUBLIC_BASE_URL", defaults.public_base_url),
            psp_url=os.getenv("TPK_PSP_URL", defaults.psp_url),
            psp_webhook_secret=os.getenv("TPK_PSP_WEBHOOK_SECRET", defaults.psp_webhook_secret),
            airline_url=os.getenv("TPK_AIRLINE_URL", defaults.airline_url),
            airline_timeout_s=float(os.getenv("TPK_AIRLINE_TIMEOUT_S", defaults.airline_timeout_s)),
            hold_ttl_s=float(os.getenv("TPK_HOLD_TTL_S", defaults.hold_ttl_s)),
        )
