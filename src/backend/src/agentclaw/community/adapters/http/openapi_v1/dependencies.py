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

When the shared key does not resolve — no secret name registered, no such
secret, an empty value, or a secret store that is down — what happens depends
on the environment, and this seam only ever sees one of the two cases:

- in ``pre``/``prod`` the process never gets here at all. ``app.py`` calls
  ``init_principal_verifier_config(..., strict=True)``, which raises during
  import, so no request is served and the rollout fails loudly.
- everywhere else it boots with an empty key and **every public request answers
  ``401``** — the same state this surface has been in since it was defined, now
  reached by denying rather than by a stub.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.core.gateway_principal import (
    PrincipalType,
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
    config = get_principal_verifier_config()
    token = request.headers.get(PRINCIPAL_HEADER, "").strip()
    if not token:
        # Distinguished from a rejected token, and on its own log line, because
        # the two point at completely different things. A *missing* header is
        # not an auth failure at all: the gateway injects it on every forwarded
        # request, so its absence means this request did not come through the
        # gateway, or came through one whose route table does not require an
        # identity for this path. Chasing signing keys for that is chasing the
        # wrong half of the system, which is exactly what the previous silence
        # invited.
        logger.warning(
            "no %s header on %s %s (request did not arrive through the "
            "gateway's authenticated path; verifier key fp=%s)",
            PRINCIPAL_HEADER,
            request.method,
            request.url.path,
            config.key_fingerprint,
        )
        return None
    try:
        return verify_principal_token(token, config)
    except PrincipalVerificationError as exc:
        # Log the reason (never the token) and treat the caller as absent. The
        # request goes on to answer 401 from require_principal. The reason
        # carries the fingerprint of the key this component judged the token
        # against, plus the token's own JOSE header marked as caller-supplied —
        # see ``core/gateway_principal/verifier.py``. The fingerprint is the
        # part to reason from: compared against the gateway's boot line it
        # separates a key mismatch from an expiry or a wrong audience, while
        # the header is unauthenticated and can say anything a forger likes.
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


async def require_user_and_app_principal(
    caller: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """Require the signed caller to contain both User and App identities."""
    if not any(principal.type == PrincipalType.APP for principal in caller.principals):
        raise MissingPrincipalError("no verified user-and-app caller for this request")
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
