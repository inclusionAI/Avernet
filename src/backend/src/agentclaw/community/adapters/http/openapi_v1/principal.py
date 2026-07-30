"""Caller-identity extraction for the public API.

The single place that turns the ``require_principal`` dependency's value into the
caller's owner id used to scope every service call. It's isolated here so that
the shape of a verified principal is one module's business, not every handler's.

The owner id scopes reads/writes to the caller's own bots *within* the tenant
(the tenant guard confines data to the tenant; this confines it to the caller).

The gateway verifier now supplies a
:class:`~agentclaw.community.core.gateway_principal.VerifiedCaller`, whose
``user_id`` is the owner anchor derived from the identity set — and which is
empty for an ``app`` or ``access_key`` caller, because neither the gateway's
``app.owners`` free-text field nor its owner-less access-key registry names a
person. Such a caller lands on the "carries no user_id" branch below and gets a
``401`` rather than a guessed owner. That tolerance of a bare string or a mapping
is what let this helper survive the stub-to-real swap unchanged.
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
