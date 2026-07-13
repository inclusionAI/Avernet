"""Bearer token verification for /api/economy/governance/records/offline-batch.

Compares the request's Bearer token against ``EconomyInternalToken.value``,
which EconomyGovernanceModule has already resolved from SecretResolver.
Singlebox / tests fall back to a local token constant; pre/prod resolve the
real secret from Mist.

Used by the offline-batch endpoint — its callers (ODPS pipeline /
upload_governance_data.py) have no user session (cookie/SSO), so API-friendly
static Bearer token is the only viable auth. card-callback instead goes through
the cookie/SSO ``RequestContext`` chain (see router.py).

Failure mode: any auth problem returns 401, never reveals whether the token
was empty, malformed, or simply wrong. Mirrors bot_dormant/auth.py.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.di.config import EconomyInternalToken


async def verify_economy_internal_token(
    authorization: str | None = Header(None, description="Bearer <token>"),
    token_cfg: EconomyInternalToken = Injected(EconomyInternalToken),
) -> None:
    """Raise 401 unless the request carries the configured Bearer token.

    ``authorization`` is optional at the header-extraction layer (``Header(None)``)
    so a missing header yields a uniform 401 here rather than a 422 that would
    leak "header required" to the caller. Constant-time-ish comparison via
    Python's ``==`` (token length is short and not user-controlled, so a timing
    attack is not realistic here).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    # No token configured = the feature is effectively off; reject all calls.
    if not token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
    if token != token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")