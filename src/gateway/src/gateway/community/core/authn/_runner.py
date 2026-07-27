"""Auth runner — produce an Identities set for a request (spec §7, rev 3).

For each required identity type, run its configured ordered plugin chain:

- a plugin returning ``None`` (not applicable / no credential) **falls through**
  to the next plugin in the chain;
- a plugin raising ``AuthError`` (applicable but invalid) is **terminal** — no
  fallback, so a bad credential can never be masked by a later plugin;
- the first plugin that returns a ``Principal`` wins for that type.

If a type's chain is exhausted with no ``Principal`` (all declined), raise
``AuthError`` (fail-closed) for that type. The runner never reads the ``source``
header; plugin self-selection is fully inside each plugin's ``build``.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AuthStrategy,
    CredentialBundle,
    Identities,
    Principal,
    PrincipalType,
)

from ._route_security import Requirement


async def authenticate(
    creds: CredentialBundle,
    requirement: Requirement,
    registry: dict[PrincipalType, tuple[AuthStrategy, ...]],
) -> Identities:
    """Build an Identities set for the request, or raise ``AuthError`` (401)."""
    collected: dict[PrincipalType, Principal] = {}
    for ptype in requirement:
        chain = registry.get(ptype)
        if chain is None:  # misconfigured: required type has no plugin chain
            raise AuthError(f"no auth strategy registered for type: {ptype}")
        principal = await _run_chain(ptype, creds, chain)
        collected[ptype] = principal
    return Identities(collected)


async def _run_chain(
    ptype: PrincipalType, creds: CredentialBundle, chain: tuple[AuthStrategy, ...]
) -> Principal:
    for strategy in chain:
        # A plugin raising AuthError is terminal: present-but-invalid credential,
        # never masked by a later plugin.
        principal = await strategy.build(creds)
        if principal is not None:
            return principal
    raise AuthError(f"unauthenticated: no credential for {ptype}")
