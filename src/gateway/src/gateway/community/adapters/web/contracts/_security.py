"""Per-route required-identity-types metadata (OpenAPI ``x-avernet-security``).

Each public route declares the identity **types** it requires via the
``x-avernet-security`` extension. The gateway's route-security compiler resolves
the most-specific rule per request (spec §8). v1 routes require an authenticated
**user** by default; routes that need a bot (or bot+user) declare so.

The marker lists identity-type strings (e.g. ``["user"]``, ``["bot", "user"]``).
It declares *types*, not strategy names — which plugin chain produces each type
is configured in ``authn.yaml`` and is orthogonal to routes.
"""

from typing import Any

from gateway.community.spi.authn import PrincipalType


def requires_identities(*types: PrincipalType) -> dict[str, Any]:
    """OpenAPI extra marking a route as requiring the given identity types."""
    return {"x-avernet-security": [str(t) for t in types]}


def requires_user_principal() -> dict[str, Any]:
    """Convenience for the common case: a single authenticated user identity."""
    return requires_identities(PrincipalType.USER)
