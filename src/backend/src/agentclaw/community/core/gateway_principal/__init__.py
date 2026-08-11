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
    MIN_SIGNING_KEY_BYTES,
    PrincipalVerifierConfig,
    VerifiedCaller,
    is_weak_signing_key,
    key_fingerprint,
    verify_principal_token,
)

__all__ = [
    "MIN_SIGNING_KEY_BYTES",
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
    "is_weak_signing_key",
    "key_fingerprint",
    "verify_principal_token",
]
