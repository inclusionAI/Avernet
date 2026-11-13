"""Bot-only trust boundary; no SSO/user fallback and no identity from the body."""

from fastapi import HTTPException, Request

from agentclaw.community.core.gateway_principal import (
    GatewayBot,
    PrincipalVerificationError,
    verify_bot_principal_token,
)
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)


async def require_commission_bot(request: Request) -> GatewayBot:
    try:
        return verify_bot_principal_token(
            request.headers.get("X-Avernet-Principal", ""),
            get_principal_verifier_config(),
        )
    except PrincipalVerificationError as exc:
        # Do not reveal issuer/signature/expiry distinctions or log credentials.
        raise HTTPException(status_code=401, detail="Unauthorized") from exc
