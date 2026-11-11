"""
Simple Token-based Authentication Provider

OSS-friendly authentication using simple API tokens.

Dev mode (BCSFUSE_PROVIDER_MODE=dev or dev_smoke):
  Auth is bypassed — all requests are accepted without a token.
  This is intentional: dev mode is for local development only and should
  never be exposed to untrusted networks.

Runtime mode:
  All requests require a valid Bearer token matching BCSFUSE_AUTH_TOKEN.
"""
import os
from typing import Any, Optional


class SimpleTokenAuthProvider:
    """
    Simple token-based authentication provider.

    Validates tokens against a configured token value.
    Suitable for OSS deployments where internal auth systems are not available.

    In dev/dev_smoke mode, authentication is bypassed for developer convenience.
    In runtime mode, a valid Bearer token is required.
    """

    def __init__(self, valid_token: Optional[str] = None):
        """Initialize auth provider.

        Args:
            valid_token: The valid API token. If None, reads from BCSFUSE_AUTH_TOKEN env var.
        """
        self.valid_token = valid_token or os.getenv("BCSFUSE_AUTH_TOKEN", "")
        self._dev_mode = os.getenv("BCSFUSE_PROVIDER_MODE", "dev") in ("dev", "dev_smoke")

    def authenticate(self, token: str) -> Optional[dict]:
        """Authenticate a token and return user info if valid.

        Args:
            token: The token to authenticate.

        Returns:
            User info dict if valid, None otherwise.
        """
        # Dev mode: accept any token (including empty)
        if self._dev_mode:
            return {
                "user_id": "dev_user",
                "username": "dev_user",
                "roles": ["admin"],
                "authenticated": True,
            }

        if not token or not self.valid_token:
            return None

        if token == self.valid_token:
            return {
                "user_id": "api_user",
                "username": "api_user",
                "roles": ["user"],
                "authenticated": True,
            }

        return None

    def get_current_user(self, request: Any) -> Optional[dict]:
        """Get current user from request.

        Args:
            request: The request object (expected to have headers).

        Returns:
            User info dict if valid, None otherwise.
        """
        if not hasattr(request, "headers"):
            return None

        # Try Authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return self.authenticate(token)

        # Try X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return self.authenticate(api_key)

        # Dev mode: accept requests with no token at all
        if self._dev_mode:
            return self.authenticate("")

        return None

    def has_permission(self, user: dict, permission: str) -> bool:
        """Check if user has a specific permission.

        Args:
            user: User info dict.
            permission: Permission to check.

        Returns:
            True if user has permission, False otherwise.
        """
        if not user or not user.get("authenticated"):
            return False

        # Simple permission model for OSS: all authenticated users have all permissions
        return True

    def validate_request(self, request: Any) -> bool:
        """Validate if request has valid authentication.

        Args:
            request: The request object.

        Returns:
            True if valid, False otherwise.
        """
        user = self.get_current_user(request)
        return user is not None and user.get("authenticated", False)