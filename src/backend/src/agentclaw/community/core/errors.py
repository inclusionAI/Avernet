"""Domain-level error hierarchy raised by ``core/``.

These exceptions carry semantic meaning **only** — no HTTP status,
no transport concern of any kind. The adapter layer (``api/app.py``)
owns the mapping from each domain error class to an HTTP status code
and emits ``{"detail": <detail>}`` JSON. A different adapter (gRPC,
CLI, internal RPC) would supply its own mapping.

Rule 7 (``docs/arch/arch.rules.md``) — Core Independence — forbids
``core/`` from depending on transport/framework concerns. This module
is the replacement for every ``HTTPException`` raise that used to live
in ``core/``.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base class for all core-layer errors.

    Subclasses describe *semantic* situations (resource missing, caller
    not authenticated, input invalid). They deliberately carry no
    HTTP status — that's an adapter-layer translation choice; see
    ``agentclaw.community.adapters.http.app._DOMAIN_ERROR_STATUS_MAP``.

    ``detail`` is the human-readable message; adapters typically echo
    it back to the caller.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ValidationError(DomainError):
    """Caller-supplied input failed validation."""


class Unauthorized(DomainError):
    """Caller is not authenticated."""


class LoginRedirectRequired(DomainError):
    """Caller has no valid session and must restart login.

    Translated by the HTTP adapter into the pre-existing (non-standard)
    302-with-JSON-body shape that the frontend depends on — see
    ``specs/2026-05-21-rule7-httpexception-out-of-core``. The class
    name describes the *situation*; the 302 lives at the boundary.
    """


class Forbidden(DomainError):
    """Caller is authenticated but lacks permission."""


class NotFound(DomainError):
    """Requested resource does not exist."""


class Conflict(DomainError):
    """Request conflicts with current resource state."""


class InternalError(DomainError):
    """Unexpected failure inside a core service."""
