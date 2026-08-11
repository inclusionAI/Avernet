"""Auth runner — resolve every identity a route requires (§7, reshaped).

For each identity declared in the route's requirement (``{identity: required|
optional}``), run that identity's :class:`~gateway.community.core.authn._chain.IdentityChain`:

- the chain returns a ``Principal`` → adopt it;
- the chain raising ``AuthError`` (present but invalid) → propagate immediately
  (terminal, no fallback — design §5), regardless of required/optional;
- the chain returning ``None`` (exhausted, no credential):
    * REQUIRED  → raise ``AuthError`` (identity missing);
    * OPTIONAL  → the identity is simply absent from the result set;
- no chain registered for an identity → misconfig, terminal ``AuthError``.

Returns the set of identities that were present, keyed by ``PrincipalType``.
"""

from __future__ import annotations

from gateway.community.core.authn._chain import IdentityChain
from gateway.community.core.authn._route_security import Requirement
from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Presence,
    Principal,
    PrincipalType,
)

logger = get_logger("authn-runner")


async def authenticate(
    creds: CredentialBundle,
    requirement: Requirement,
    registry: dict[PrincipalType, IdentityChain],
) -> dict[PrincipalType, Principal]:
    """Resolve every required/present identity, or raise ``AuthError`` (401/403)."""
    resolved: dict[PrincipalType, Principal] = {}
    for identity, presence in requirement.items():
        chain = registry.get(identity)
        if chain is None:  # misconfigured identity → fail closed, terminal
            logger.warning("no auth strategy registered for type: %s", identity.value)
            raise AuthError(f"no auth strategy registered for type: {identity.value}")
        # AuthError from a present-but-invalid credential propagates and is
        # terminal (required or optional alike); None means the chain found no
        # applicable credential, and Presence decides accept-vs-deny.
        principal = await chain.build(creds)
        if principal is None:
            if presence is Presence.REQUIRED:
                logger.warning("required identity %s has no credential", identity.value)
                raise AuthError(f"unauthenticated: no credential for {identity.value}")
            logger.debug("optional identity %s absent", identity.value)
            continue  # OPTIONAL + exhausted → absent from the result set
        logger.debug("identity %s resolved", identity.value)
        resolved[identity] = principal
    return resolved
