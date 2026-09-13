"""Small fixed-delay retry helpers for Handler HTTP calls."""

from __future__ import annotations

import time
from typing import Callable, TypeVar


T = TypeVar("T")
ATTEMPTS = 5
DELAY_SECONDS = 3


def retry_http(operation: Callable[[], T], *, label: str = "HTTP request") -> T:
    last_error: Exception | None = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt >= ATTEMPTS:
                raise
            print(
                f"[http-retry] {label} failed attempt={attempt}/{ATTEMPTS}; "
                f"retryInSeconds={DELAY_SECONDS}; error={type(exc).__name__}: {exc}",
                flush=True,
            )
            time.sleep(DELAY_SECONDS)
    assert last_error is not None
    raise last_error
