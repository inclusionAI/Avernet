"""``first_party_user`` strategy — builds a UserPrincipal from a session cookie.

For our own frontend / a human with a login session. The browser can't carry a
tenant token (it's a secret), so the tenant is taken from the authenticated
user's identity, falling back to a configured default (auth design §4.6, §6.2).
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError, AuthPlugin
from gateway.community.spi.authn import (
    CredentialBundle,
    Delegation,
    Principal,
    StrategyParams,
    UserPrincipal,
)

# Cookies that indicate a first-party login session is present.
_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")


class FirstPartyUserStrategy:
    """Resolve a login-session cookie into a :class:`UserPrincipal`.

    Implements the ``AuthStrategy`` protocol structurally (``name`` + ``build``);
    the composition root registers it in an ``AuthStrategy``-typed registry,
    where mypy verifies conformance.
    """

    name = "first_party_user"

    def __init__(self, auth: AuthPlugin, default_tenant: str) -> None:
        self._auth = auth
        self._default_tenant = default_tenant  # fallback when identity has no tenant

    async def build(
        self, creds: CredentialBundle, params: StrategyParams
    ) -> Principal | None:
        if not any(name in creds.cookies for name in _SESSION_COOKIES):
            return None  # no first-party session → strategy not applicable
        if params.delegation is Delegation.FORBIDDEN:
            raise AuthError(
                "route forbids a user identity but a session cookie is present"
            )
        # Invalid session → AuthPlugin raises AuthError (hard failure).
        user = await self._auth.get_login_user(
            cookie=creds.headers.get("cookie", ""),
            referer=creds.headers.get("referer"),
        )
        # Browser can't present a tenant token; take it from the identity.
        tenant = user.tenant_id or self._default_tenant
        # Scope vocabulary is out of scope this session — no scopes granted yet.
        return UserPrincipal(tenant=tenant, subject=user, scopes=frozenset())
