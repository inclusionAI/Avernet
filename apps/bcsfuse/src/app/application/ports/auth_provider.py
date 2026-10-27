from typing import Any, Protocol


class AuthProvider(Protocol):
    """Public authentication provider contract.

    Implementations may be OSS defaults or internal plugins.
    Public code must depend on this contract, not internal auth SDKs.
    """

    def authenticate(self, token: str) -> dict | None:
        """Authenticate a token and return user info.

        Args:
            token: Authentication token

        Returns:
            User info dict if authenticated, None otherwise.
        """
        ...

    def get_current_user(self, request: Any) -> dict | None:
        """Get current authenticated user from request.

        Args:
            request: HTTP request object

        Returns:
            User info dict if authenticated, None otherwise.
        """
        ...

    def has_permission(self, user: dict, permission: str) -> bool:
        """Check if user has a specific permission.

        Args:
            user: User info dict
            permission: Permission string

        Returns:
            True if user has permission, False otherwise.
        """
        ...

    def validate_request(self, request: Any) -> bool:
        """Return whether an incoming request is authenticated.

        Args:
            request: HTTP request object

        Returns:
            True when the request carries valid authentication.
        """
        ...
