from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifyResult:
    allowed: bool
    idempotency_key: str | None = None
    error_message: str | None = None
