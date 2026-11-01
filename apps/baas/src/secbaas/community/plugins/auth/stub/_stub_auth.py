"""StubAuthPlugin — test double for AuthPlugin."""

from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote

from secbaas.community.spi.auth import AuthPlugin, AuthUser


class StubAuthPlugin(AuthPlugin):
    """Stub implementation of AuthPlugin for tests.

    Returns the configured default user unless a local identity cookie is
    present, and always allows permissions.
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
        local_user = self._user_from_local_cookie(cookie)
        if local_user is not None:
            return local_user
        return self._default_user

    def _user_from_local_cookie(self, cookie: str | None) -> AuthUser | None:
        """Resolve the standard Singlebox/local identity cookie when present."""
        if not cookie:
            return None

        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(cookie)
        except CookieError:
            return None

        identity = next(
            (
                parsed[key].value
                for key in ("staff_id", "entity_id")
                if key in parsed and parsed[key].value
            ),
            None,
        )
        if identity is None:
            return None

        raw_nick = parsed["nick_name"].value if "nick_name" in parsed else identity
        return self._default_user.model_copy(
            update={
                "id": identity,
                "operatorName": identity,
                "outUserNo": identity,
                "nickName": unquote(raw_nick),
            }
        )

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
