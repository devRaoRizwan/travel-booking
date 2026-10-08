import time
from datetime import datetime, timezone


# Timestamps are stored as epoch milliseconds.
def now_ms() -> int:
    return int(time.time() * 1000)


def iso(epoch_ms: int | None) -> str | None:
    if epoch_ms is None:
        return None
    timestamp = datetime.fromtimestamp(epoch_ms / 1000, timezone.utc)
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
