"""Recovery lifecycle — periodic lease-and-resume of non-terminal tasks.

A ``Lifecycle`` participant driven by the application lifespan: ``startup()``
launches a daemon thread that periodically calls
``TaskRecoveryWorker.recover_once`` and heartbeats active leases; ``shutdown()``
cancels the loop. Discovery is automatic once the DI module binds this class as
a singleton (``discover_lifecycle_participants`` introspects the injector).

To keep lifecycle discovery cheap, the constructor only stores the injector and
resolves the graph repository + task service lazily on first use (the task
service graph is heavier and only needed once recovery is actually enabled).

Enablement is explicit and off by default so singlebox/test stay deterministic:
set ``TASK_RECOVERY_ENABLED=1`` (or any truthy value) in the deployment profile
to activate. Lease/interval/instance identity are likewise environment-driven.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Optional

from injector import Injector, inject

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskGraphRepositoryProtocol,
)
from agentclaw.community.core.task.task_center.recovery import TaskRecoveryWorker
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_server_host

logger = get_logger()

_DEFAULT_INTERVAL = 30      # seconds between recovery scans
_DEFAULT_LEASE = 60         # seconds a recovery lease is held
_FALSY = {"", "0", "false", "no", "off"}


def _flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip().lower()


class TaskRecoveryLifecycle(LifecycleBase):
    """Periodic recovery worker driver — auto-discovered via ``LifecycleBase``."""

    @inject
    def __init__(self, injector: Injector) -> None:
        self._injector = injector
        self._enabled = _flag("TASK_RECOVERY_ENABLED") not in _FALSY
        self._interval = int(os.environ.get("TASK_RECOVERY_INTERVAL", _DEFAULT_INTERVAL))
        self._lease_seconds = int(os.environ.get("TASK_RECOVERY_LEASE_SECONDS", _DEFAULT_LEASE))
        self._instance_id = os.environ.get("TASK_RECOVERY_INSTANCE_ID") or get_server_host()
        self._worker: Optional[TaskRecoveryWorker] = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _resolve_worker(self) -> Optional[TaskRecoveryWorker]:
        """Lazily resolve graph repository + task service and build the worker.

        Returns ``None`` if either dependency is not bound (lightweight/core-only
        injectors), in which case recovery is a no-op even when enabled.
        """
        if self._worker is not None:
            return self._worker
        try:
            graph_repo = self._injector.get(TaskGraphRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑图仓储 → 无可恢复图
            logger.info("[task-recovery] graph repository not bound, recovery disabled")
            return None
        try:
            task_service = self._injector.get(TaskServiceProtocol)
        except Exception as exc:  # noqa: BLE101 任务服务装配失败 → 本周期跳过
            logger.warning("[task-recovery] task service not resolvable: %s", exc)
            return None
        self._worker = TaskRecoveryWorker(
            graph_repo,
            resume=task_service.redrive_task,
            instance_id=self._instance_id,
            lease_seconds=self._lease_seconds,
        )
        return self._worker

    async def startup(self) -> None:
        if not self._enabled:
            logger.info("[task-recovery] disabled (set TASK_RECOVERY_ENABLED=1 to enable)")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "[task-recovery] started: interval=%ss lease=%ss instance=%s",
            self._interval, self._lease_seconds, self._instance_id,
        )

    async def shutdown(self) -> None:
        if not self._enabled or self._thread is None:
            return
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[task-recovery] stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            worker = self._resolve_worker()
            if worker is not None:
                try:
                    recovered = asyncio.run(worker.recover_once(limit=100))
                    if recovered:
                        logger.info("[task-recovery] recovered tasks=%s", recovered)
                except Exception:
                    logger.exception("[task-recovery] scan error")
            # honor shutdown without sleeping the full interval when asked to stop
            self._stop_event.wait(timeout=self._interval)
