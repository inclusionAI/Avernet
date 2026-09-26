"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions and ``_protocols`` for the strategy
contract; the auth design doc (``src/gateway/docs/2026-07-21-auth-design.md``)
has the full picture. The bot / access-key / app registry contracts live in
their own domain SPIs: ``gateway.community.spi.bot`` /
``gateway.community.spi.access_key`` / ``gateway.community.spi.app``.
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
from ._protocols import AuthStrategy

__all__ = [
    "AccessKey",
    "AccessKeyPrincipal",
    "AppPrincipal",
    "AuthStrategy",
    "Bot",
    "BotPrincipal",
    "CredentialBundle",
    "Presence",
    "Principal",
    "PrincipalType",
    "ThirdPartyApp",
    "UserPrincipal",
]
