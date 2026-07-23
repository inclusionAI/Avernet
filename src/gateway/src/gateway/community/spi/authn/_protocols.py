"""Authn SPI — the ``AuthStrategy`` contract (how a Principal is built).

A strategy is a named way to turn a request's credentials into a
:class:`~gateway.community.spi.authn.Principal`. The gateway holds a small,
closed set of them; each route names the one(s) it accepts via its
``x-avernet-security`` marker (see the auth design doc §5).
"""

from __future__ import annotations

from typing import Protocol

from ._models import CredentialBundle, Principal, StrategyParams


class AuthStrategy(Protocol):
    """Builds a Principal from a request, or signals inapplicability."""

    name: str  # stable id referenced by x-avernet-security

    async def build(
        self, creds: CredentialBundle, params: StrategyParams
    ) -> Principal | None:
        """Try to build a Principal (tenant included) from the request.

        Returns ``None`` when this strategy's credential is **absent** — not
        applicable, so the next OR branch may try. Raises ``AuthError`` when the
        credential is **present but invalid** (hard failure, no fallback).
        Returns a ``Principal`` on success; the runner then checks scope.
        """
        ...
