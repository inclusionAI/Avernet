"""``google`` strategy — resolve a *presented* Google access token in place.

The caller presents a Google OAuth access token in a request header (the token
Google issued after its own consent/login flow). This strategy verifies it by
calling Google's userinfo endpoint directly and maps the response onto a
:class:`UserPrincipal`.

There is no cookie/session fallback — the gateway's ``user`` identity comes
only from a verified Google access token. ``None`` (no token presented) leaves
it to the runner to fail-close (401); a present-but-unverifiable token raises
:class:`AuthError` (terminal, no fallback).

``transport`` is an HTTP-transport seam: production omits it (real network);
tests pass an :class:`httpx.MockTransport` so the userinfo call is not made
against the real Google endpoint.
"""

from __future__ import annotations

import httpx

from gateway.community.spi.auth import AuthenticatedUser, AuthError
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)

# Google userinfo endpoint — called with a bearer access token to resolve the
# caller's identity.
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# Per-request timeout for the userinfo call, so a hung Google endpoint cannot
# pin the gateway request indefinitely.
USERINFO_TIMEOUT_SECONDS = 10.0


class GoogleUserStrategy:
    """Resolve a presented Google access token into a :class:`UserPrincipal`."""

    name = "google"
    principal_type = PrincipalType.USER

    def __init__(
        self,
        *,
        token_header: str,
        default_tenant: str | None = None,
        userinfo_url: str = GOOGLE_USERINFO_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_header = token_header
        self._default_tenant = default_tenant
        self._userinfo_url = userinfo_url
        self._transport = transport

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = creds.headers.get(self._token_header, "")
        if not token:
            return None  # no presented token → not applicable; runner fail-closes
        async with httpx.AsyncClient(
            timeout=USERINFO_TIMEOUT_SECONDS, transport=self._transport
        ) as client:
            resp = await client.get(
                self._userinfo_url, headers={"Authorization": f"Bearer {token}"}
            )
        if resp.status_code != 200:
            raise AuthError("invalid google user token")
        try:
            body = resp.json()
            sub = body["sub"]
        except (ValueError, KeyError) as ex:
            raise AuthError("invalid google userinfo response") from ex
        subject = AuthenticatedUser(
            id=sub,
            username=body.get("email") or sub,
            display_name=body.get("name"),
        )
        return UserPrincipal(tenant=self._default_tenant, subject=subject)
