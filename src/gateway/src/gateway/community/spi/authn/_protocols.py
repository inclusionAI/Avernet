"""Authn SPI — the ``AuthStrategy`` contract (how a Principal is built).

A strategy is a named way to turn a request's credentials into a
:class:`~gateway.community.spi.authn.Principal` of a specific identity type.
"""

from __future__ import annotations

from typing import Protocol

from ._models import CredentialBundle, Principal, PrincipalType


class AuthStrategy(Protocol):
    """Builds a Principal of a specific type from a request, or declines."""

    name: str  # stable id referenced by authn.yaml chains
    principal_type: PrincipalType  # the identity type this strategy produces

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try to build a Principal from the request.

        Returns ``None`` (not applicable / no credential) → runner falls through.
        Raises ``AuthError`` (applicable but invalid) → terminal, no fallback.
        Returns a ``Principal`` → success.
        """
        ...
