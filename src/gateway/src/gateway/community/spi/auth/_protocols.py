"""Auth plugin Protocol — unified authentication + authorization contract."""

from __future__ import annotations

from typing import Protocol

from ._models import AuthenticatedUser


class AuthPlugin(Protocol):
    """Plugin protocol for authentication and authorization.

    Unifies login, whitelist, and permission checking into a single contract.

    Implementations:
    - BareAuthPlugin: returns hardcoded user, always-allowed for tests.
    - SofaAuthPlugin (enterprise): calls buservice API for login.
    """

    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthenticatedUser:
        """Authenticate user and return user profile.

        Args:
            cookie: User cookie from HTTP request.
            referer: Referer header from HTTP request.

        Returns:
            AuthenticatedUser with user profile information.

        Raises:
            AuthError: If authentication fails.
        """
        ...

    def is_allowed(self, user: AuthenticatedUser) -> bool:
        """Check whether the user is in the operator whitelist.

        Args:
            user: The authenticated AuthenticatedUser to check.

        Returns:
            True if the user's operatorName is in the whitelist.
        """
        ...

    def check_permission(
        self,
        user_id: str,
        permission_codes: str,
        request_url: str = "",
        request_map: str = "",
    ) -> bool:
        """Check whether a user has the specified permissions.

        Args:
            user_id: User staff ID (工号).
            permission_codes: Comma-separated permission codes to check.
            request_url: Optional request URL context.
            request_map: Optional request mapping context.

        Returns:
            True if the user has ALL specified permissions.
        """
        ...
