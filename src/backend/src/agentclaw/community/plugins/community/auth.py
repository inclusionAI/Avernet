"""OidcAuthPlugin — community AuthPlugin backed by BCS unified auth.

NOTE: the class name is retained for historical reasons (DI bindings and call
sites are unchanged). The implementation no longer verifies OIDC JWTs locally —
in the community / singlebox deployment there is no corporate SSO, and BCS is
the unified-auth entry point. This plugin delegates identity resolution to BCS's
``GET /auth/user``, forwarding the inbound cookies (the BCS session cookie
``bcs_session`` rides along automatically).

Deployment prerequisite: BCS must have OAuth configured (``[auth.oauth]`` with
``jwt_secret``, a valid ``base_url``, and at least one provider); otherwise
``GET /auth/user`` returns 404 and this plugin raises ``RuntimeError``.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx
from injector import inject

from agentclaw.community.core.errors import Forbidden, Unauthorized
from agentclaw.community.di import config_community as cfg
from agentclaw.community.plugin_api.auth import (
    AuthenticatedIdentity,
    AuthPlugin,
    AuthRequestContext,
)


class OidcAuthPlugin(AuthPlugin):
    """Community AuthPlugin: resolve identity via BCS ``GET /auth/user``."""

    @inject
    def __init__(self, bcs_config: cfg.BcsAuthConfig) -> None:
        self._cfg = bcs_config
        # Test seam: when set, ``_fetch_userinfo`` returns this dict instead of
        # calling BCS. ``Callable[[str], dict]`` keyed on the Cookie header.
        self._userinfo_resolver: Callable[[str], dict[str, Any]] | None = None
        # Test seam: when set, the httpx client is built with this transport so
        # the network path can be driven offline.
        self._transport: httpx.AsyncBaseTransport | None = None

    # -- AuthPlugin interface --------------------------------------------------

    async def get_login_user(
        self,
        cookie: str,
        referer: str | None = None,
    ) -> AuthenticatedIdentity:
        """Resolve identity from a serialized cookie string (forwarded as-is)."""
        if not cookie or not cookie.strip():
            raise Unauthorized("Authentication required: missing cookie")
        info = await self._fetch_userinfo(cookie)
        return self._identity_from_userinfo(info)

    async def resolve_user_from_request(
        self,
        ctx: AuthRequestContext,
    ) -> AuthenticatedIdentity:
        """Resolve identity by forwarding the request's cookies to BCS."""
        cookie_header = self._cookie_header(ctx.cookies)
        if not cookie_header:
            raise Unauthorized("Authentication required: missing cookie")
        info = await self._fetch_userinfo(cookie_header)
        return self._identity_from_userinfo(info)

    def is_operator_allowed(self, staff_id: str) -> bool:
        """Operator policy: membership in the configured subject allowlist."""
        return staff_id in self._cfg.operator_subjects

    async def authorize_entity_access(
        self,
        ctx: AuthRequestContext,
        requested_entity_id: str | None,
        requested_entity_type: str | None,
    ) -> tuple[str | None, str | None]:
        """Self-only entity-access policy (provider-agnostic).

        Requires an authenticated subject; defaults a missing ``entity_id`` to
        the caller's own id; forbids querying any other subject's entity.
        ``Unauthorized`` (missing/invalid session) and ``RuntimeError`` (BCS
        unreachable / misconfigured) propagate to the api layer.
        """
        identity = await self.resolve_user_from_request(ctx)
        if not identity.staffId:
            raise Unauthorized("Authentication required")
        if not requested_entity_id:
            return identity.staffId, requested_entity_type or "staff"
        if requested_entity_id != identity.staffId:
            raise Forbidden("无权查询其他用户的资源")
        return requested_entity_id, requested_entity_type

    # -- BCS delegation --------------------------------------------------------

    @staticmethod
    def _cookie_header(cookies: dict[str, str]) -> str:
        """Rebuild a ``Cookie`` header from a cookie dict (skips empty values)."""
        return "; ".join(
            f"{k}={v}"
            for k, v in cookies.items()
            if v
        )

    def _identity_from_userinfo(
        self, info: dict[str, Any]
    ) -> AuthenticatedIdentity:
        user_id = info.get("user_id")
        if not user_id:
            raise Unauthorized("BCS /auth/user returned no user_id")
        uid = str(user_id)
        # BCS has no separate work-number / account name; user_id backs both
        # the canonical handle (staffId) and operatorName.
        return AuthenticatedIdentity(
            id=uid,
            operatorName=uid,
            outUserNo=uid,
            nickName=info.get("name"),
        )

    async def _fetch_userinfo(self, cookie_header: str) -> dict[str, Any]:
        if self._userinfo_resolver is not None:
            return self._userinfo_resolver(cookie_header)
        url = f"{self._cfg.base_url.rstrip('/')}{self._cfg.user_path}"
        try:
            async with httpx.AsyncClient(
                timeout=self._cfg.timeout, transport=self._transport
            ) as client:
                resp = await client.get(url, headers={"Cookie": cookie_header})
        except httpx.HTTPError as exc:
            # Network failure / timeout — surface as a deployment problem.
            raise RuntimeError(f"BCS /auth/user request failed: {exc}") from exc
        return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code == 401:
            raise Unauthorized("not authenticated (bcs)")
        if resp.status_code != 200:
            # 404 = BCS /auth/* not mounted (OAuth not configured); 5xx etc.
            raise RuntimeError(
                f"BCS /auth/user returned {resp.status_code} "
                "(is BCS OAuth configured?)"
            )
        return resp.json()
