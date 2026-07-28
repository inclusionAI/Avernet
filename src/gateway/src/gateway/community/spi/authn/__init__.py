"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the strategy
contract, ``_ports`` for the app-token / tenant dependency ports, and the auth
design doc (``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
The bot / access-key registry contracts live in their own domain SPIs:
``gateway.community.spi.bot`` / ``gateway.community.spi.access_key``.
"""

from ._models import (
    AccessKey,
    AccessKeyPrincipal,
    AppPrincipal,
    Bot,
    BotPrincipal,
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)
from ._ports import (
    AppTokenRecord,
    AppTokenValidator,
    TenantResolver,
)
from ._protocols import AuthStrategy

__all__ = [
    "AccessKey",
    "AccessKeyPrincipal",
    "AppPrincipal",
    "AppTokenRecord",
    "AppTokenValidator",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "CredentialBundle",
    "Presence",
    "Principal",
    "PrincipalType",
    "TenantResolver",
    "ThirdPartyApp",
    "UserPrincipal",
]
