"""AuthContext — cross-cutting auth principal for plugin calls.

The web layer constructs an `AuthContext` per request and passes it to every
plugin method it invokes. Plugins route per-token (upstream gateway connection
pooling, tenant isolation, etc.) by reading `auth.token`; plugins that don't
care ignore it.

`None` is a valid default — it represents "no auth known at this call site."
OpenClaw falls back to a shared module-level upstream client in that case.

Kept deliberately small. The HTTP delivery layer may construct this context from an
`AuthenticatedPrincipal` installed in the ASGI scope by trusted upstream
authentication middleware. Query, body, and ordinary headers are never
valid sources for the principal.
"""
from __future__ import annotations

from dataclasses import dataclass


AUTHENTICATED_PRINCIPAL_SCOPE_KEY = "authenticated_principal"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Principal installed by trusted upstream authentication middleware."""

    user_id: str
    token: str | None = None


@dataclass(frozen=True)
class AuthContext:
    """Auth principal for a single plugin call.

    Carries the verified token and actor identity for a single plugin call.
    ``user_id`` is populated only from a trusted ``AuthenticatedPrincipal``;
    it must not be filled from request business parameters.
    """

    token: str | None = None
    user_id: str | None = None


__all__ = [
    "AUTHENTICATED_PRINCIPAL_SCOPE_KEY",
    "AuthenticatedPrincipal",
    "AuthContext",
]
