"""Authn SPI — the ``AuthStrategy`` contract.

A strategy is a named way to turn a request's credentials into a
:class:`~gateway.community.spi.authn.Principal`. The gateway holds an ordered
chain of strategies per :class:`~gateway.community.spi.authn.PrincipalType`;
each strategy either recognises the credential and builds a Principal, returns
``None`` (inapplicable — let the next strategy try), or raises (hard failure).
A route names the identities it requires via ``x-avernet-security`` (design §8).
"""

from __future__ import annotations

from typing import Protocol

from ._models import CredentialBundle, Principal, PrincipalType


class AuthStrategy(Protocol):
    """Builds a Principal for one identity type, or signals inapplicability."""

    name: str  # stable id (the PrincipalType value)
    principal_type: PrincipalType  # the identity type this strategy produces

    async def build(self, creds: CredentialBundle) -> Principal | None:
        """Try to build a Principal from the request.

        Returns ``None`` when this identity's credential is **absent** — not
        applicable. Raises :class:`~gateway.community.spi.auth.AuthError` when the
        credential is **present but invalid** (hard failure, no fallback).
        Returns a ``Principal`` on success.
        """
        ...
