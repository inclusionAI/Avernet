"""OAuthPlugin — AuthPlugin implementation backed by BCS /auth/user."""

from __future__ import annotations

from secbaas.logger import get_logger
from secbaas.spi.auth import AuthPlugin, AuthUser

from ._oauth_client import OAuthClient

logger = get_logger("plugin-auth-oauth")


class OAuthPlugin(AuthPlugin):
    """AuthPlugin backed by BCS `GET /auth/user`.

    Authentication is delegated to BCS (session-cookie / OAuth). Authorization
    is open: `is_allowed` and `check_permission` always return True, because
    BCS exposes no whitelist/permission API. Intended for singlebox/standalone
    deployments where BCS is the trusted identity source.

    Precondition: the target BCS must have OAuth configured, otherwise
    `/auth/user` is not mounted and returns 404.
    """

    def __init__(self, oauth_client: OAuthClient | None = None) -> None:
        self._oauth_client = oauth_client or OAuthClient()

    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthUser:
        logger.info(
            "OAuthPlugin login attempt: cookie=%s referer=%s", bool(cookie), referer
        )
        return await self._oauth_client.get_login_user(cookie=cookie, referer=referer)

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
