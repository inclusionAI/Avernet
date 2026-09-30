"""Development cookie authn strategy for local browser-based integration.

This strategy is intentionally inert outside local/dev/test environments. It lets
local frontends that already carry an internal ``staff_id`` cookie exercise the
public OpenAPI gateway path without requiring a Google OAuth token.
"""

from __future__ import annotations

import os
from urllib.parse import unquote

from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
    UserPrincipal,
)


class DevCookieUserStrategy:
    """Resolve a local developer user from an internal staff-id cookie."""

    name = "dev_cookie"
    principal_type = PrincipalType.USER

    def __init__(
        self,
        *,
        staff_id_cookie: str = "staff_id",
        fallback_staff_id_cookie: str = "__TRACERT_COOKIE_bucUserId",
        nick_name_cookie: str = "nick_name",
        enabled_envs: tuple[str, ...] = ("local", "dev", "test"),
    ) -> None:
        self._staff_id_cookie = staff_id_cookie
        self._fallback_staff_id_cookie = fallback_staff_id_cookie
        self._nick_name_cookie = nick_name_cookie
        self._enabled_envs = {env.lower() for env in enabled_envs}

    async def build(self, creds: CredentialBundle) -> Principal | None:
        if os.getenv("SERVER_ENV", "").strip().lower() not in self._enabled_envs:
            return None

        staff_id = (
            creds.cookies.get(self._staff_id_cookie)
            or creds.cookies.get(self._fallback_staff_id_cookie)
            or ""
        ).strip()
        if not staff_id:
            return None

        display_name = (
            unquote(creds.cookies.get(self._nick_name_cookie, "")).strip() or staff_id
        )
        subject = AuthenticatedUser(
            id=staff_id,
            username=staff_id,
            display_name=display_name,
        )
        return UserPrincipal(subject=subject)
