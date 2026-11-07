from typing import Protocol, Any, Optional


class ContextProvider(Protocol):
    """Public context provider contract.

    Implementations may be OSS defaults (no-op) or internal plugins (BCN).
    Public code must depend on this contract, not internal context SDKs.

    Context providers supply group context, user context, and other
    contextual information needed for business logic.
    """

    async def get_context(
        self,
        *,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get context information.

        Args:
            group_id: Optional group ID for group context
            user_id: Optional user ID for user context

        Returns:
            Context dictionary with group/user information.

        Note:
            For OSS implementations, this may return an empty dict.
            For internal implementations, this may query BCN for context.
        """
        ...

    async def set_context(
        self,
        context: dict[str, Any],
        *,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Set context information.

        Args:
            context: Context dictionary to set
            group_id: Optional group ID for group context
            user_id: Optional user ID for user context

        Note:
            For OSS implementations, this may be a no-op.
            For internal implementations, this may set context in BCN.
        """
        ...