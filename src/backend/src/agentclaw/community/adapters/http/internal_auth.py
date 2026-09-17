"""Bearer token verification for routes with no end-user credential.

The general-purpose sibling of ``bot_dormant.auth`` and
``skill_center.internal_auth``: those verify a token granted for one operation,
this one verifies ``InternalApiToken`` — the shared token a machine-to-machine
route checks when it has no user identity to authorize against.

Use it as a router-level dependency::

    router = APIRouter(
        prefix="/api/public/bots",
        dependencies=[Depends(verify_internal_api_token)],
    )

so a route added later inherits the guard instead of shipping open.

Failure mode: any auth problem returns 401, never revealing whether the token
was missing, malformed, or simply wrong. An empty resolved token (a corp
deployment with no secret name registered, or a resolver outage) closes the
routes rather than opening them.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.di.config import InternalApiToken

_BEARER_PREFIX = "Bearer "


async def verify_internal_api_token(
    authorization: str | None = Header(None, description="Bearer <token>"),
    token_cfg: InternalApiToken = Injected(InternalApiToken),
) -> None:
    """Raise 401 unless the request carries the configured Bearer token.

    The header is declared optional so a request that omits it is answered by
    this guard (401) rather than by FastAPI's request validation (422): a
    missing credential is an auth failure, not a malformed request.
    """
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[len(_BEARER_PREFIX):]
    # No token configured = these routes are off; reject rather than compare
    # against "" (which a header of "Bearer " would otherwise match).
    if not token_cfg.value:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not hmac.compare_digest(token, token_cfg.value):
        raise HTTPException(status_code=401, detail="Invalid token")
