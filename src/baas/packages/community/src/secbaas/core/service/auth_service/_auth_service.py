"""AuthService — domain-oriented auth API wrapping AuthPlugin."""

from __future__ import annotations

from secbaas.api import OperationContext
from secbaas.api.auth import AuthService as AuthServiceProtocol
from secbaas.core.utils import env_utils
from secbaas.spi.auth import AuthError, AuthPlugin, AuthUser


class AuthService(AuthServiceProtocol):
    """Application-level auth service.

    Wraps an AuthPlugin and provides a clean domain API for core services
    and web dependencies.
    """

    def __init__(self, plugin: AuthPlugin) -> None:
        if plugin is None:
            raise ValueError("plugin is required")
        self._plugin = plugin

    async def authenticate_request(
        self, cookie: str, referer: str | None = None
    ) -> AuthUser:
        """Authenticate a user from an HTTP request.

        Args:
            cookie: Full cookie string from the HTTP request.
            referer: Referer header from the HTTP request.

        Returns:
            AuthUser for the authenticated user.

        Raises:
            AuthError: If authentication fails.
        """
        try:
            return await self._plugin.get_login_user(cookie=cookie, referer=referer)
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(f"Authentication failed: {exc}") from exc

    async def build_operation_context(
        self, cookie: str, referer: str
    ) -> OperationContext:
        """Build an OperationContext from cookie and referer.

        Resolves the authenticated user via cookie/referer and wraps
        the result into an OperationContext with environment info.
        """
        user = await self.authenticate_request(cookie=cookie, referer=referer)
        return OperationContext(
            operator=user.staffId,
            env=env_utils.get_current_env(),
        )

    def check_user_permission(self, user: AuthUser, permission_codes: str) -> bool:
        """Check whether a user has the specified permissions.

        Args:
            user: The authenticated user.
            permission_codes: Comma-separated permission codes.

        Returns:
            True if the user has ALL specified permissions.
        """
        return self._plugin.check_permission(
            user_id=user.staffId,
            permission_codes=permission_codes,
        )

    def is_operator(self, user: AuthUser) -> bool:
        """Check whether the user is an allowed operator.

        Args:
            user: The authenticated user.

        Returns:
            True if the user's operator name is whitelisted.
        """
        return self._plugin.is_allowed(user)
