"""Public startup lifecycle for a durable, rebuildable vector index."""

import logging
from typing import Any

from src.application.ports.startup_provider import StartupProvider

logger = logging.getLogger(__name__)


class VectorIndexStartupProvider:
    """Initialize dependencies, rebuild the index, and close it on shutdown."""

    def __init__(self, delegate: StartupProvider, vector_store: Any) -> None:
        self._delegate = delegate
        self._vector_store = vector_store

    async def initialize(self) -> None:
        await self._delegate.initialize()
        try:
            rebuild = getattr(self._vector_store, "rebuild_from_backend", None)
            if rebuild is None:
                rebuild = getattr(self._vector_store, "rebuild_from_mysql", None)
            if rebuild is None:
                raise TypeError("durable vector store does not support rebuild")
            result = rebuild()
            logger.info("[VectorIndexStartup] Rebuild completed: %s", result)
        except Exception:
            await self._delegate.shutdown()
            raise

    async def shutdown(self) -> None:
        try:
            close = getattr(self._vector_store, "close", None)
            if close is not None:
                close()
        finally:
            await self._delegate.shutdown()
