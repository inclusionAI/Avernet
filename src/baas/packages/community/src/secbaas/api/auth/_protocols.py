"""Auth service protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from secbaas.api import OperationContext


@runtime_checkable
class AuthService(Protocol):
    """Protocol for the auth service — resolves OperationContext from HTTP metadata."""

    async def build_operation_context(
        self, cookie: str, referer: str
    ) -> OperationContext:
        """Build an OperationContext from cookie and referer strings.

        Args:
            cookie: Full cookie string from the HTTP request.
            referer: Referer URL from the HTTP request.

        Returns:
            OperationContext with authenticated operator and environment info.
        """
        ...
