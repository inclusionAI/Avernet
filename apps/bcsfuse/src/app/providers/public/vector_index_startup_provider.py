"""Public startup lifecycle for a durable, rebuildable vector index."""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.application.ports.startup_provider import StartupProvider

logger = logging.getLogger(__name__)


class VectorIndexStartupProvider:
    """Initialize dependencies, rebuild the index, and close it on shutdown."""

    def __init__(
        self,
        delegate: StartupProvider,
        vector_store: Any,
        *,
        recovery_interval_seconds: float = 5.0,
    ) -> None:
        self._delegate = delegate
        if recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be positive")
        self._vector_store = vector_store
        self._recovery_interval_seconds = recovery_interval_seconds
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._recovery_stop = threading.Event()
        self._recovery_thread: threading.Thread | None = None

    async def initialize(self) -> None:
        await self._delegate.initialize()
        heartbeat_thread = self._start_owner_lease_heartbeat()
        try:
            if getattr(self._vector_store, "read_only", False):
                synchronize = getattr(
                    self._vector_store, "synchronize_read_view", None
                )
                if synchronize is None:
                    raise TypeError(
                        "read-only vector store does not support view synchronization"
                    )
                result = synchronize()
                self._mark_startup_rebuild_recovered(self._vector_store)
                logger.info(
                    "[VectorIndexStartup] Read-only view synchronization completed: %s",
                    result,
                )
            else:
                rebuild = getattr(self._vector_store, "rebuild_from_backend", None)
                if rebuild is None:
                    rebuild = getattr(self._vector_store, "rebuild_from_mysql", None)
                if rebuild is None:
                    raise TypeError("durable vector store does not support rebuild")
                result = rebuild()
                self._mark_startup_rebuild_recovered(self._vector_store)
                logger.info("[VectorIndexStartup] Rebuild completed: %s", result)
        except Exception as error:
            self._mark_startup_rebuild_failed(self._vector_store, error)
            self._start_rebuild_recovery()
            logger.exception(
                "[VectorIndexStartup] Index startup operation failed; "
                "starting degraded with local fallback"
            )

    @staticmethod
    def _mark_startup_rebuild_failed(vector_store: Any, error: Exception) -> None:
        """Surface the failed durable rebuild in readiness and diagnostics."""
        setattr(vector_store, "startup_rebuild_failed", True)
        setattr(vector_store, "startup_rebuild_error_type", type(error).__name__)

    @staticmethod
    def _mark_startup_rebuild_recovered(vector_store: Any) -> None:
        """Clear a historical startup failure once rebuild succeeds again."""
        setattr(vector_store, "startup_rebuild_failed", False)
        setattr(vector_store, "startup_rebuild_error_type", None)

    def _start_rebuild_recovery(self) -> None:
        """Retry the safe startup operation without blocking the serving loop."""

        if self._recovery_thread is not None:
            return
        if getattr(self._vector_store, "read_only", False):
            operation = getattr(self._vector_store, "synchronize_read_view", None)
            operation_name = "Read-only view synchronization"
        else:
            operation = getattr(self._vector_store, "rebuild_from_backend", None)
            if operation is None:
                operation = getattr(self._vector_store, "rebuild_from_mysql", None)
            operation_name = "Rebuild"
        if operation is None:
            return
        self._recovery_stop.clear()
        self._recovery_thread = threading.Thread(
            target=self._run_rebuild_recovery,
            args=(operation, operation_name),
            daemon=True,
            name="index-startup-rebuild-recovery",
        )
        self._recovery_thread.start()

    def _run_rebuild_recovery(self, operation: Any, operation_name: str) -> None:
        while not self._recovery_stop.wait(self._recovery_interval_seconds):
            try:
                result = operation()
            except Exception as error:
                logger.debug(
                    "[VectorIndexStartup] Rebuild recovery attempt failed: %s",
                    type(error).__name__,
                )
                continue
            self._mark_startup_rebuild_recovered(self._vector_store)
            logger.info(
                "[VectorIndexStartup] %s recovery completed: %s",
                operation_name,
                result,
            )
            return

    def _stop_rebuild_recovery(self) -> None:
        thread = self._recovery_thread
        if thread is None:
            return
        self._recovery_stop.set()
        thread.join()
        self._recovery_thread = None

    async def shutdown(self) -> None:
        self._stop_rebuild_recovery()
        try:
            self._stop_owner_lease_heartbeat()
            close = getattr(self._vector_store, "close", None)
            if close is not None:
                close()
        finally:
            await self._delegate.shutdown()

    def _start_owner_lease_heartbeat(self) -> threading.Thread | None:
        renew = getattr(self._vector_store, "renew_owner_lease", None)
        interval = getattr(self._vector_store, "lease_heartbeat_seconds", None)
        if not callable(renew) or not isinstance(interval, (int, float)):
            return None
        interval_seconds = float(interval)
        if interval_seconds <= 0 or interval_seconds >= _lease_seconds(self._vector_store):
            logger.warning(
                "[VectorIndexStartup] Skipping owner lease heartbeat: "
                "interval must be positive and below the lease"
            )
            return None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._run_owner_lease_heartbeat,
            args=(renew, interval_seconds, self._heartbeat_stop),
            daemon=True,
            name="index-owner-lease-heartbeat",
        )
        self._heartbeat_thread.start()
        return self._heartbeat_thread

    @staticmethod
    def _run_owner_lease_heartbeat(
        renew: Any, interval_seconds: float, stop: threading.Event
    ) -> None:
        while not stop.wait(interval_seconds):
            try:
                renewed = bool(renew())
                if not renewed:
                    logger.debug(
                        "[VectorIndexStartup] Replica is read-only; owner lease heartbeat remains active"
                    )
            except Exception as error:
                logger.warning(
                    "[VectorIndexStartup] Owner lease renewal failed: %s",
                    type(error).__name__,
                )

    def _stop_owner_lease_heartbeat(self) -> None:
        thread = self._heartbeat_thread
        if thread is None:
            return
        self._heartbeat_stop.set()
        thread.join()
        self._heartbeat_thread = None


def _lease_seconds(vector_store: Any) -> float:
    value = getattr(vector_store, "lease_seconds", float("inf"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


__all__ = ["VectorIndexStartupProvider"]
