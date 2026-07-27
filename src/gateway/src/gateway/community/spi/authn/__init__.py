"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the
``AuthStrategy`` contract, ``_identities`` for the request identity set, and the
auth design doc (``src/gateway/docs/2026-07-21-auth-design.md``) for the full
picture. The bot-token registry composition lives inside ``BotTokenStrategy``
(no separate validator/SPI).
"""

from ._identities import Identities
from ._models import (
    BotPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)
from ._protocols import AuthStrategy

__all__ = [
    "AuthStrategy",
    "BotPrincipal",
    "CredentialBundle",
    "Identities",
    "Principal",
    "PrincipalType",
    "UserPrincipal",
]
