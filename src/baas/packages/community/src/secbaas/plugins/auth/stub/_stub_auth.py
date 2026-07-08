"""StubAuthPlugin — test double for AuthPlugin."""

from __future__ import annotations

from secbaas.spi.auth import AuthPlugin, AuthUser


class StubAuthPlugin(AuthPlugin):
    """Stub implementation of AuthPlugin for tests.

    Returns a hardcoded user and always-allowed permissions.
    """

    def __init__(
        self,
        default_user: AuthUser | None = None,
    ) -> None:
        self._default_user = default_user or AuthUser(
            id="stub-user-001",
            operatorName="stub_operator",
            staffId="000001",
            nickName="StubUser",
            realName="Stub User",
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
