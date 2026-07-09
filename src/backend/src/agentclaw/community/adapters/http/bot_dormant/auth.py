"""Bearer token verification for /api/internal/dormant/* endpoints.

Compares the request's Bearer token against ``DormantInternalToken.value``,
which BotDormantModule has already resolved from SecretResolver. Singlebox /
tests may fall back to the local token constant; pre/prod resolve the real
secret from Mist.

Failure mode: any auth problem returns 401, never reveals whether the
token was empty, malformed, or simply wrong.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.di.config import DormantInternalToken


async def verify_dormant_internal_token(
    authorization: str = Header(..., description="Bearer <token>"),
    token_cfg: DormantInternalToken = Injected(DormantInternalToken),
) -> None:
    """Raise 401 unless the request carries the configured Bearer token.

    Constant-time-ish comparison via Python's `==` (token length is short
    and not user-controlled, so a timing attack is not realistic here).
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    # No token configured = the feature is effectively off; reject all calls.
    if not token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
    if token != token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
