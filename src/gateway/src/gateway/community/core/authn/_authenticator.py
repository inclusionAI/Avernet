"""Authenticator domain class — transport-agnostic auth orchestration.

``Authenticator`` ties identity chains and route security to the core
runner. It is a domain object, not bootstrap wiring — adapters receive it
via ``app.state`` and never import plugins or core.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.community.logger import get_logger
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle, Principal, PrincipalType

from ._chain import IdentityChain
from ._route_security import RouteSecurity
from ._runner import authenticate as run_auth

logger = get_logger("authenticator")


@dataclass(frozen=True)
class Authenticator:
    """Resolves a route's identity requirement and runs its identity chains."""

    strategies: dict[PrincipalType, IdentityChain]
    route_security: RouteSecurity

    async def authenticate(
        self, method: str, path: str, creds: CredentialBundle
    ) -> dict[PrincipalType, Principal]:
        requirement = self.route_security.resolve(method, path)
        if requirement is None:  # fail-closed: no policy → deny
            raise AuthError("no auth policy for route")
        logger.info(
            "authn resolved %s %s requirement=%s",
            method,
            path,
            {k.value: v.value for k, v in requirement.items()},
        )
        return await run_auth(creds, requirement, self.strategies)
