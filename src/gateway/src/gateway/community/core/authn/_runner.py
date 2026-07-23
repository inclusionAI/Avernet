"""Auth runner — execute a route's requirement to produce a Principal (§7).

Given the request credentials, a route's requirement (OR-list of alternatives),
and the strategy registry, try each alternative in order:

- a strategy returning ``None`` (credential absent) fails that alternative and
  falls through to the next one;
- a strategy raising ``AuthError`` (credential present but invalid) is
  **terminal** — it propagates immediately, with no OR fallback, so a bad
  credential can never be masked by a later alternative;
- a Principal with insufficient scope fails that alternative;
- otherwise the alternative succeeds and its Principal is adopted.

If no alternative applies, raise the last error (fail-closed).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import AuthStrategy, CredentialBundle, Principal

from ._route_security import Requirement


async def authenticate(
    creds: CredentialBundle,
    requirement: Requirement,
    registry: dict[str, AuthStrategy],
) -> Principal:
    """Build a Principal for the request, or raise ``AuthError`` (401/403)."""
    last_err: AuthError | None = None
    for alternative in requirement:  # OR across alternatives
        built: Principal | None = None
        ok = True
        for name, params in alternative.items():  # AND within an alternative
            strategy = registry.get(name)
            if strategy is None:  # misconfigured route → fail closed, terminal
                raise AuthError(f"unknown auth strategy: {name}")
            # A strategy raising AuthError means the credential is present but
            # INVALID — that is terminal (no OR fallback), so let it propagate.
            # Only a None result (credential absent) falls through to the next
            # alternative.
            principal = await strategy.build(creds, params)
            if principal is None:  # credential absent → this alternative is N/A
                ok = False
                break
            if not params.scopes <= principal.scopes:  # required ⊆ granted
                ok, last_err = False, AuthError("insufficient scope")
                break
            built = principal
        if ok and built is not None:
            return built
    raise last_err or AuthError("unauthorized")
