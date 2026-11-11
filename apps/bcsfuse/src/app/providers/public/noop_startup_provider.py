"""
No-Op Startup Provider

OSS-friendly startup provider that performs no initialization.
Suitable for open-source deployments without internal startup infrastructure.
"""
import logging

logger = logging.getLogger(__name__)


class NoopStartupProvider:
    """
    No-op startup provider for OSS deployments.

    This provider performs no initialization or shutdown operations.
    It's suitable for open-source deployments that don't require
    internal startup infrastructure like Sofapy, MOSN, or Layotto.

    For internal deployments, use the internal startup provider that
    initializes Sofapy, MOSN, Layotto, and other internal infrastructure.
    """

    async def initialize(self) -> None:
        """Initialize the application startup.

        For OSS deployments, this is a no-op.
        No internal infrastructure is initialized.
        """
        logger.info("NoopStartupProvider: initialize (no-op)")
        # No-op for OSS deployments

    async def shutdown(self) -> None:
        """Shutdown the application gracefully.

        For OSS deployments, this is a no-op.
        No internal infrastructure is shutdown.
        """
        logger.info("NoopStartupProvider: shutdown (no-op)")
        # No-op for OSS deployments