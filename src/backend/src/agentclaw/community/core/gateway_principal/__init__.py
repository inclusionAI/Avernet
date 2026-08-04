"""Gateway principal verification — the backend half of auth design §7.1.

Verifies the gateway-signed ``X-Avernet-Principal`` token and projects it onto
backend DTOs. The HTTP seam that uses this lives in
``adapters/http/openapi_v1/dependencies.py``; the environment-driven config in
``utils/gateway_principal_config.py``.
"""

from __future__ import annotations

from agentclaw.community.core.gateway_principal.errors import (
    PrincipalVerificationError,
)
from agentclaw.community.core.gateway_principal.models import (
    AccessKeyPrincipal,
    AppPrincipal,
    BotPrincipal,
    GatewayAccessKey,
    GatewayApp,
    GatewayBot,
    GatewayPrincipal,
    GatewayUser,
    PrincipalType,
    UserPrincipal,
)
from agentclaw.community.core.gateway_principal.verifier import (
    PrincipalVerifierConfig,
    VerifiedCaller,
    key_fingerprint,
    verify_principal_token,
)

__all__ = [
    "AccessKeyPrincipal",
    "AppPrincipal",
    "BotPrincipal",
    "GatewayAccessKey",
    "GatewayApp",
    "GatewayBot",
    "GatewayPrincipal",
    "GatewayUser",
    "PrincipalType",
    "PrincipalVerificationError",
    "PrincipalVerifierConfig",
    "UserPrincipal",
    "VerifiedCaller",
    "key_fingerprint",
    "verify_principal_token",
]
