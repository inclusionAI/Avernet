"""
Qdrant Sync Manager (Open-Core Stub - ZDAS Only)

This module is a stub for open-core. The real QdrantSyncManager is internal-only
and manages ZDAS synchronization for QdrantZdasVectorStore.

For open-core, use QdrantLocalVectorStore which doesn't require synchronization.

The actual implementation is available at:
- bcsfuse_internal.providers.vector.QdrantZdasVectorStoreProvider
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)


class ZdasSyncManagerUnavailable(RuntimeError):
    """Raised when attempting to use QdrantSyncManager in open-core."""
    pass


class QdrantSyncManager:
    """
    Qdrant 同步管理器 (Open-Core Stub - ZDAS Only)

    This is a stub for open-core. The real QdrantSyncManager is internal-only
    and manages ZDAS synchronization.

    For open-core, use QdrantLocalVectorStore which doesn't require synchronization.
    """

    def __init__(
        self,
        vector_store: object,
        incremental_interval: int = 60,
        full_sync_interval_minutes: int = 1440,
        enable_auto_sync: bool = True,
    ):
        """
        初始化同步管理器（Stub）

        Args:
            vector_store: 向量存储实例（忽略）
            incremental_interval: 增量同步间隔（忽略）
            full_sync_interval_minutes: 全量同步间隔（忽略）
            enable_auto_sync: 是否启用自动同步（忽略）

        Raises:
            ZdasSyncManagerUnavailable: 总是抛出，说明这是 open-core stub
        """
        raise ZdasSyncManagerUnavailable(
            "QdrantSyncManager is internal-only and has moved to "
            "bcsfuse_internal.providers.vector. Open-core must use "
            "QdrantLocalVectorStore which doesn't require ZDAS synchronization. "
            "For ZDAS support, use bcsfuse_internal provider wiring."
        )

    def maybe_incremental_sync(self) -> dict[str, int]:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def force_incremental_sync(self) -> dict[str, int]:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def do_full_sync(self, purge_before_days: int = 0) -> dict:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def start_scheduler(self) -> bool:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def stop_scheduler(self) -> bool:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def get_status(self) -> dict:
        """Stub - Always raises error."""
        raise ZdasSyncManagerUnavailable("QdrantSyncManager is ZDAS-only (internal)")

    def is_enabled(self) -> bool:
        """Stub - Always returns False."""
        return False