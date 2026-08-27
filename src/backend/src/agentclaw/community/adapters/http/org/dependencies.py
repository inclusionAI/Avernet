"""Access seam for ``GET /api/v1/org/user`` — verify the gateway's forwarded
principal, refusing uniformly when there is no caller we can trust.

The gateway authenticates the caller and forwards the identity set it resolved
as a signed ``X-Avernet-Principal`` token; this module is the access end of
that contract for the ``/api/v1/org/user`` surface.

It reuses the cached, signature-verified :func:`resolve_caller` — the same seam
the ``/openapi/v1/*`` surface and the access log read — rather than
re-implementing verification, so signature verification stays in one place.
``None`` (absent header, a bad/expired/wrong-audience token, or an unconfigured
verifier) yields no caller and a uniform ``401``; the specific reason is
logged, never returned.

Policy difference from the ``/openapi/v1/org/user`` sibling: an app-only caller
is **not** refused here. Any verified principal may look a user up; the
``user_id`` parameter names the directory subject, not the actor.
"""

from __future__ import annotations

from starlette.requests import HTTPConnection

from agentclaw.community.adapters.http.openapi_v1.dependencies import resolve_caller
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import VerifiedCaller


async def require_org_user_caller(connection: HTTPConnection) -> VerifiedCaller:
    """Return the verified caller, or refuse with the surface's uniform ``401``.

    Delegates the signature verification to :func:`resolve_caller` (cached per
    request) and owns only the access decision: a request with no verified
    caller answers ``401``, indistinguishable by reason — missing header or a
    forged token look the same from outside, by design.
    """
    caller = resolve_caller(connection)
    if caller is None:
        raise MissingPrincipalError("no verified caller for /api/v1/org/user")
    return caller
