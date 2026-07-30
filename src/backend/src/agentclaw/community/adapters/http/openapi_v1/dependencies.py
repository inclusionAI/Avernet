"""Auth seam for the public API — verify the gateway's forwarded principal.

The gateway authenticates the caller and forwards the identity set it resolved as
a signed ``X-Avernet-Principal`` token (auth design §7.1); this module is the
backend end of that contract. It holds the two seams the rest of the public
surface was built against, and nothing else changes when they go live:

- :func:`require_principal` — the FastAPI dependency every public route already
  declares. Returns the verified caller, or raises so the route answers ``401``.
- :func:`resolve_avernet_tenant` — the data-isolation tenant for the request,
  read by ``AvernetTenantMiddleware`` before any route runs.

**Both read the same header, and verification happens once.** The middleware runs
first, so it does the work and caches the outcome on the request scope; the
dependency reuses it. Verifying twice would be wasted signature work and, worse,
a window in which the two seams could disagree about who the caller is.

Failure is uniform on purpose. No header, a bad signature, an expired token, an
audience meant for another component, a payload we cannot parse — every one of
them yields *no caller* and a ``401`` carrying the same fixed message. The
specific reason is logged, never returned: telling a caller which part of a
forged token to fix is telling them how to forge the next one.

When the key is unconfigured (``AVERNET_PRINCIPAL_SIGNING_KEY`` unset) every
public request still answers ``401`` — the same state this surface has been in
since it was defined, now reached by denying rather than by a stub.
"""

from __future__ import annotations

from fastapi import Request

from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import (
    PrincipalVerificationError,
    VerifiedCaller,
    verify_principal_token,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)

logger = get_logger()

# The header the gateway injects. It strips any inbound copy before forwarding,
# so a caller cannot supply their own — but that is the gateway's guarantee, not
# ours: we verify the signature regardless of where the header came from.
PRINCIPAL_HEADER = "X-Avernet-Principal"

# Where the resolved caller is cached for the life of the request. Starlette's
# ``request.state`` is backed by the ASGI scope dict by reference, so the value
# the middleware stores is the value the route's own ``Request`` reads.
_CALLER_STATE_ATTR = "avernet_public_caller"


class _Unset:
    """Sentinel type distinguishing "not yet resolved" from a resolved ``None``."""


_UNSET = _Unset()

# The verified caller, as the public routers see it. They only ever hand this to
# ``caller_owner_id``, so the concrete type is an implementation detail of this
# seam — but it is now a real type rather than the ``Any`` the stub used.
Principal = VerifiedCaller


def _resolve_caller(request: Request) -> VerifiedCaller | None:
    """Verify the forwarded principal once per request, caching the outcome.

    ``None`` means "no caller we can trust" — absent header or failed
    verification, deliberately indistinguishable to the caller. The cache holds
    ``None`` too, so a failed verification is not retried on the same request.
    """
    cached = getattr(request.state, _CALLER_STATE_ATTR, _UNSET)
    if cached is not _UNSET:
        return cached

    caller = _verify_from_headers(request)
    setattr(request.state, _CALLER_STATE_ATTR, caller)
    return caller


def _verify_from_headers(request: Request) -> VerifiedCaller | None:
    """Verify the request's principal header, logging why if it fails."""
    token = request.headers.get(PRINCIPAL_HEADER, "").strip()
    if not token:
        return None
    try:
        return verify_principal_token(token, get_principal_verifier_config())
    except PrincipalVerificationError as exc:
        # Log the reason (never the token) and treat the caller as absent. The
        # request goes on to answer 401 from require_principal.
        logger.warning(
            "rejected forwarded principal on %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )
        return None


async def require_principal(request: Request) -> Principal:
    """Return the verified caller, or raise :class:`MissingPrincipalError` (401)."""
    caller = _resolve_caller(request)
    if caller is None:
        raise MissingPrincipalError("no verified caller for this request")
    return caller


def resolve_avernet_tenant(request: Request) -> str:
    """Return the data-isolation tenant a public-API request belongs to.

    The single seam for the public API's tenant: one implementation for every
    deploy profile (there is one gateway contract), not a per-profile DI binding.

    The tenant comes straight from the verified principal — the gateway's tenant
    id *is* the isolation key, which is the whole point of Track A. There is no
    translation table, so a gateway tenant must be spelled exactly as the
    ``avernet_tenant`` column stores it.

    With no trustworthy caller this falls back to the internal default, which is
    what every non-public path resolves to anyway. That is safe because the
    request cannot get data out: every public route depends on
    :func:`require_principal` and therefore answers ``401`` first — a property
    pinned by ``test_public_routes_require_principal``, not left to inspection.
    """
    caller = _resolve_caller(request)
    if caller is None:
        return DEFAULT_AVERNET_TENANT
    return caller.tenant
