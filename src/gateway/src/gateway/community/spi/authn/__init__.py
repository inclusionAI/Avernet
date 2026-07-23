"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions, ``_protocols`` for the
``AuthStrategy`` contract, and the auth design doc
(``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
"""

from ._models import (
    CredentialBundle,
    Delegation,
    Principal,
    PrincipalType,
    StrategyParams,
    UserPrincipal,
)
from ._protocols import AuthStrategy

__all__ = [
    "AuthStrategy",
    "CredentialBundle",
    "Delegation",
    "Principal",
    "PrincipalType",
    "StrategyParams",
    "UserPrincipal",
]
