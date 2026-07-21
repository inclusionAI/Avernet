"""BareAuthPlugin — test double for AuthPlugin.

Returns a hardcoded user and always-allowed permissions so the
open-source edition runs without an identity backend.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthPlugin, AuthUser


class BareAuthPlugin(AuthPlugin):
    """Stub implementation of AuthPlugin for bare mode.

    Returns a hardcoded user and always-allowed permissions.
    """

    def __init__(
        self,
        default_user: AuthUser | None = None,
    ) -> None:
        self._default_user = default_user or AuthUser(
            id="bare-user-001",
            operatorName="bare_operator",
            staffId="000001",
            nickName="BareUser",
            realName="Bare User",
        )

    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthUser:
        return self._default_user

    def is_allowed(self, user: AuthUser) -> bool:
        return True

    def check_permission(
        self,
        user_id: str,
        permission_codes: str,
        request_url: str = "",
        request_map: str = "",
    ) -> bool:
        return True
