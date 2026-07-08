"""Profile-neutral auth-gate bridge for inbound personal chat messages."""
from __future__ import annotations

from dataclasses import dataclass
from engine.community.plugin_api.auth_gate.protocol import AuthGateService


@dataclass
class AuthGateResult:
    allowed: bool
    idempotency_key: str | None = None
    error_message: str | None = None


async def verify_chat_send(
    *,
    auth_gate_service: AuthGateService,
    session_key: str,
    message: str,
    iam_token: str,
) -> AuthGateResult:
    result = await auth_gate_service.verify(
        token=iam_token,
        content=message,
        session_id=session_key,
    )
    return AuthGateResult(
        allowed=result.allowed,
        idempotency_key=result.idempotency_key,
        error_message=result.error_message,
    )
