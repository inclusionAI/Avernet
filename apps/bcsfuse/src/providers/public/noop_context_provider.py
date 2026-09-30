"""
No-Op Context Provider

OSS-friendly context provider that returns empty context.
Suitable for open-source deployments without internal group context infrastructure (BCN).
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class NoopContextProvider:
    """
    No-op context provider for OSS deployments.

    This provider returns empty context information.
    It's suitable for open-source deployments that don't have access
    to internal group context systems like BCN.

    For internal deployments, use the internal context provider that
    retrieves context from BCN or other internal context systems.

    Impact:
    - Group context features will not be available
    - User context features will not be available
    - Basic recommend task/query functionality still works
    - Fusion verify may have limited context-awareness
    """

    async def get_context(
        self,
        *,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get context information.

        For OSS deployments, this returns an empty dict.

        Args:
            group_id: Optional group ID (ignored in no-op implementation)
            user_id: Optional user ID (ignored in no-op implementation)

        Returns:
            Empty dictionary.
        """
        logger.debug(
            f"NoopContextProvider: get_context (group_id={group_id}, user_id={user_id}) - returning empty context"
        )
        return {}

    async def set_context(
        self,
        context: dict[str, Any],
        *,
        group_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Set context information.

        For OSS deployments, this is a no-op.

        Args:
            context: Context dictionary to set (ignored in no-op implementation)
            group_id: Optional group ID (ignored in no-op implementation)
            user_id: Optional user ID (ignored in no-op implementation)
        """
        logger.debug(
            f"NoopContextProvider: set_context (group_id={group_id}, user_id={user_id}) - no-op"
        )
        # No-op for OSS deployments