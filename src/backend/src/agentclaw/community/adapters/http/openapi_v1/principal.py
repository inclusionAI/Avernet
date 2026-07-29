"""Caller-identity extraction for the public API.

The single place that turns the ``require_principal`` dependency's value into the
caller's owner id used to scope every service call. It's isolated here so that
when the real gateway verifier replaces the ``require_principal`` stub, only this
helper changes — not the handlers.

The owner id scopes reads/writes to the caller's own bots *within* the tenant
(the tenant guard confines data to the tenant; this confines it to the caller).
Until the verifier lands the principal is ``None``, so every real request raises
:class:`MissingPrincipalError` (→ 401) — the correct pre-auth state.
"""

from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError


def caller_owner_id(principal: Principal) -> str:
    """Return the caller's owner id, or raise :class:`MissingPrincipalError`.

    Accepts either a bare id string or an object/mapping exposing ``user_id``,
    so it fits whatever shape the auth workstream's verified principal takes.
    """
    if principal is None:
        raise MissingPrincipalError("no authenticated caller")
    if isinstance(principal, str):
        if not principal:
            raise MissingPrincipalError("empty caller id")
        return principal
    user_id = (
        principal.get("user_id")
        if isinstance(principal, dict)
        else getattr(principal, "user_id", None)
    )
    if not user_id:
        raise MissingPrincipalError("principal carries no user_id")
    return str(user_id)
