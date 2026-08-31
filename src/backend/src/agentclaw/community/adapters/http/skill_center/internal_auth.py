"""Bearer token verification for /api/internal/skill-center/* endpoints.

Compares the request's Bearer token against ``SkillCenterInternalToken.value``,
which SkillCenterModule has already resolved from SecretResolver. Singlebox /
tests may fall back to the local token constant; pre/prod resolve the real
secret.

Failure mode: any auth problem returns 401, never revealing whether the token
was empty, malformed, or simply wrong.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.di.config import SkillCenterInternalToken


async def verify_skill_center_internal_token(
    authorization: str = Header(..., description="Bearer <token>"),
    token_cfg: SkillCenterInternalToken = Injected(SkillCenterInternalToken),
) -> None:
    """Raise 401 unless the request carries the configured Bearer token."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    # No token configured = the endpoints are effectively off; reject all calls.
    if not token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
    if token != token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
