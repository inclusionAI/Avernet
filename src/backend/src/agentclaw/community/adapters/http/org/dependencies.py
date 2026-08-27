"""Access seam for ``GET /api/v1/org/user`` — verify the gateway's forwarded
principal, refusing uniformly when there is no caller we can trust.

The gateway authenticates the caller and forwards the identity set it resolved
as a signed ``X-Avernet-Principal`` token; this module is the access end of
that contract for the ``/api/v1/org/user`` surface.

It reuses the cached, signature-verified :func:`resolve_caller` — the same seam
the ``/openapi/v1/*`` surface and the access log read — rather than
re-implementing verification, so signature verification stays in one place.
``None`` (absent header, a bad/expired token, or an unconfigured verifier)
yields no caller and a uniform ``401``; the specific reason is
logged, never returned.

Policy difference from the ``/openapi/v1/org/user`` sibling: an app-only caller
is **not** refused here. Any verified principal may look a user up; the
``user_id`` parameter names the directory subject, not the actor.
"""

from __future__ import annotations

from dataclasses import replace

from starlette.requests import HTTPConnection

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import (
    PrincipalVerificationError,
    VerifiedCaller,
    verify_principal_token,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)

logger = get_logger()
_ORDINARY_CALLER_STATE_ATTR = "avernet_ordinary_http_caller"
_UNSET = object()


async def require_org_user_caller(connection: HTTPConnection) -> VerifiedCaller:
    """Return the verified caller, or refuse with the surface's uniform ``401``.

    Performs ordinary-HTTP verification with the shared verifier and owns only
    the access decision: a request with no verified caller answers ``401``,
    indistinguishable by reason — missing header or a forged token look the
    same from outside, by design.
    """
    caller = _resolve_ordinary_http_caller(connection)
    if caller is None:
        raise MissingPrincipalError("no verified caller for ordinary HTTP request")
    return caller


async def require_user_caller(connection: HTTPConnection) -> VerifiedCaller:
    """Require a verified user principal for ordinary HTTP operations."""
    caller = _resolve_ordinary_http_caller(connection)
    if caller is None or not caller.has_user:
        raise MissingPrincipalError("verified user principal required")
    return caller


def _resolve_ordinary_http_caller(
    connection: HTTPConnection,
) -> VerifiedCaller | None:
    """Verify the ordinary-HTTP JWT without applying OpenAPI audience policy."""
    cached = getattr(connection.state, _ORDINARY_CALLER_STATE_ATTR, _UNSET)
    if cached is not _UNSET:
        return cached or None
    token = connection.headers.get(PRINCIPAL_HEADER, "").strip()
    if not token:
        setattr(connection.state, _ORDINARY_CALLER_STATE_ATTR, False)
        return None
    config = replace(get_principal_verifier_config(), verify_audience=False)
    try:
        caller = verify_principal_token(token, config)
    except PrincipalVerificationError as exc:
        logger.warning(
            "rejected ordinary HTTP principal on %s: %s",
            connection.url.path,
            exc,
        )
        caller = None
    setattr(connection.state, _ORDINARY_CALLER_STATE_ATTR, caller or False)
    return caller
