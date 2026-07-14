"""Auth plugin Protocol — unified authentication + authorization contract.

Replaces the separate IdentityPlugin + PermissionPlugin protocols.
"""

from __future__ import annotations

from typing import Protocol

from ._models import AuthUser


class AuthPlugin(Protocol):
    """Plugin protocol for authentication and authorization.

    Unifies login (IdentityPlugin), whitelist (IdentityPlugin.is_allowed),
    and permission checking (PermissionPlugin) into a single contract.

    Implementations:
    - BuserviceAuthPlugin: calls buservice API for login, antbuservice for permissions.
    - StubAuthPlugin: returns hardcoded user, always-allowed for tests.
    """

    async def get_login_user(
        self, cookie: str | None = None, referer: str | None = None
    ) -> AuthUser:
        """Authenticate user and return user profile.

        Args:
            cookie: User cookie from HTTP request.
            referer: Referer header from HTTP request.

        Returns:
            AuthUser with user profile information.

        Raises:
            RuntimeError: If API call fails.
            Exception: For 401/redirect (implementation-defined).
        """
        ...

    def is_allowed(self, user: AuthUser) -> bool:
        """Check whether the user is in the operator whitelist.

        Args:
            user: The authenticated AuthUser to check.

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
