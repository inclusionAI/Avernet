"""Bot-only trust boundary; no SSO/user fallback and no identity from the body."""

from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from agentclaw.community.core.gateway_principal import (
    BotPrincipal,
    GatewayBot,
    GatewayPrincipal,
    PrincipalVerificationError,
    PrincipalVerifierConfig,
    decode_principal_token,
)
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)

_PRINCIPAL_ADAPTER = TypeAdapter(GatewayPrincipal)


def verify_bot_principal_token(
    token: str, config: PrincipalVerifierConfig
) -> GatewayBot:
    """Verify a bot-only identity set for the bounded ACCS ingress.

    Reuses the signature/issuer/audience/expiry half from
    ``decode_principal_token`` (the public composition ``verifier.py``
    documents: authenticated is not admitted). The admission half is local to
    this commission seam: exactly one Bot principal, with nonblank
    UUID/owner/tenant and matching principal/nested Bot tenant. The gateway's
    bot UUID is opaque; never strip owner qualifiers, and do not turn a Bot
    into its human owner.

    The fail-closed ``principals`` parsing mirrors
    ``verifier._parse_principals`` (list, non-empty, DTO-validated) because
    this module deliberately keeps ``gateway_principal`` unmodified.
    """
    claims = decode_principal_token(token, config)
    raw_principals = claims.get("principals")
    if not isinstance(raw_principals, list) or not raw_principals:
        raise PrincipalVerificationError(
            "principal token carries a malformed or empty 'principals' list"
        )
    try:
        principals = tuple(
            _PRINCIPAL_ADAPTER.validate_python(item) for item in raw_principals
        )
    except ValidationError as exc:
        raise PrincipalVerificationError(
            f"principal payload does not match the expected contract: {exc}"
        ) from exc
    if len(principals) != 1 or not isinstance(principals[0], BotPrincipal):
        raise PrincipalVerificationError("expected exactly one Bot principal")
    principal = principals[0]
    bot = principal.bot
    if (
        not bot.bot_uuid.strip()
        or not bot.owner_id.strip()
        or not bot.tenant.strip()
        or bot.tenant != principal.tenant
    ):
        raise PrincipalVerificationError("invalid Bot identity or tenant")
    return bot


async def require_commission_bot(request: Request) -> GatewayBot:
    try:
        return verify_bot_principal_token(
            request.headers.get("X-Avernet-Principal", ""),
            get_principal_verifier_config(),
        )
    except PrincipalVerificationError as exc:
        # Do not reveal issuer/signature/expiry distinctions or log credentials.
        raise HTTPException(status_code=401, detail="Unauthorized") from exc
