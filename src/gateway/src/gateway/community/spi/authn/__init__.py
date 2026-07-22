"""Authn SPI — the neutral Principal contract produced by the gateway.

See ``_models`` for the model definitions and the auth design doc
(``src/gateway/docs/2026-07-21-auth-design.md``) for the full picture.
"""

from ._models import Principal, PrincipalType, UserPrincipal

__all__ = [
    "Principal",
    "PrincipalType",
    "UserPrincipal",
]
