"""Distributed periodic scheduler for Todo dispatch and status projection."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from agentclaw.community.core.repository.protocols.task.todos import (
    TodoRepositoryProtocol,
)
from agentclaw.community.core.task.todos.execution_protocol import (
    TodoExecutionServiceProtocol,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.di.todo_config import TodoSchedulerConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin

_LOCK_KEY = "todo:scheduler"


class TodoScheduler(LifecycleBase):
    """Scan A-owned states while one process holds the scheduler lease."""

    def __init__(
        self,
        repository: TodoRepositoryProtocol,
        execution: TodoExecutionServiceProtocol,
        cache: CachePlugin,
        config: TodoSchedulerConfig,
    ) -> None:
        self._repository = repository
        self._execution = execution
        self._cache = cache
        self._config = config
        self._logger = get_logger()
        self._started = False
        self._loop_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        if self._started:
            return
        if not self._config.enabled:
            self._logger.info("[todo][scheduler] disabled by configuration")
            return
        self._started = True
        self._loop_task = asyncio.create_task(self._loop())
        self._logger.info(
            "[todo][scheduler] started interval=%ss batch=%s",
            self._config.interval,
            self._config.batch_size,
        )

    async def shutdown(self) -> None:
        self._started = False
        loop_task = self._loop_task
        self._loop_task = None
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
        self._logger.info("[todo][scheduler] stopped")

    async def _loop(self) -> None:
        while self._started:
            delay = self._config.interval + random.uniform(0, self._config.jitter)
            await asyncio.sleep(delay)
            if not self._started:
                return
            try:
                result = await self.run_once()
                self._logger.info("[todo][scheduler] tick result=%s", result)
            except Exception as exc:  # noqa: BLE001 - one tick must not kill the loop
                self._logger.error("[todo][scheduler] tick failed: %s", exc)

    async def run_once(self) -> dict[str, Any]:
        """Run one operator-safe tick regardless of lifecycle enablement."""

        token = await asyncio.to_thread(
            self._cache.acquire_lock_strict,
            _LOCK_KEY,
            self._config.lock_ttl,
        )
        if token is None:
            return {"status": "lock_held", "errors": 0}

        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_lease(token=token, lease_lost=lease_lost)
        )
        errors = 0
        processed = 0
        try:
            pending = await self._list(TodoStatus.PENDING)
            result = await self._process(
                pending,
                self._dispatch,
                lease_lost,
            )
            errors += result[0]
            processed += result[1]

            if not lease_lost.is_set():
                pending_acceptance = await self._list(TodoStatus.PENDING_ACCEPTANCE)
                result = await self._process(
                    pending_acceptance,
                    self._reconcile_acceptance,
                    lease_lost,
                )
                errors += result[0]
                processed += result[1]

            if not lease_lost.is_set():
                in_progress = await self._list(TodoStatus.IN_PROGRESS)
                result = await self._process(
                    in_progress,
                    self._refresh,
                    lease_lost,
                )
                errors += result[0]
                processed += result[1]

            status = "lease_lost" if lease_lost.is_set() else "executed"
            return {"status": status, "errors": errors, "processed": processed}
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                released = await asyncio.to_thread(
                    self._cache.release_lock,
                    _LOCK_KEY,
                    token,
                )
            except Exception as exc:  # noqa: BLE001 - preserve tick outcome
                self._logger.warning("[todo][scheduler] lease release failed: %s", exc)
            else:
                if not released:
                    self._logger.warning(
                        "[todo][scheduler] lease release failed or expired"
                    )

    async def _list(self, status: TodoStatus) -> tuple[TodoRecord, ...]:
        return await asyncio.to_thread(
            self._repository.list_todos_by_status,
            status=status,
            limit=self._config.batch_size,
        )

    async def _process(
        self,
        todos: tuple[TodoRecord, ...],
        operation: Callable[[TodoRecord], Awaitable[TodoRecord]],
        lease_lost: asyncio.Event,
    ) -> tuple[int, int]:
        errors = 0
        processed = 0
        for todo in todos:
            if lease_lost.is_set():
                break
            try:
                await operation(todo)
            except Exception as exc:  # noqa: BLE001 - isolate one Todo
                errors += 1
                self._logger.error(
                    "[todo][scheduler] todo=%s status=%s failed: %s",
                    todo.todo_id,
                    todo.status.value,
                    exc,
                )
            processed += 1
        return errors, processed

    async def _dispatch(self, todo: TodoRecord) -> TodoRecord:
        return await self._execution.dispatch_pending(todo_id=todo.todo_id)

    async def _reconcile_acceptance(self, todo: TodoRecord) -> TodoRecord:
        return await self._execution.reconcile_pending_acceptance(
            todo_id=todo.todo_id
        )

    async def _refresh(self, todo: TodoRecord) -> TodoRecord:
        result = await self._execution.refresh_in_progress(todo_id=todo.todo_id)
        if result.status is not TodoStatus.IN_PROGRESS:
            return result
        return await self._execution.resume_claimed_interaction(
            todo_id=todo.todo_id,
        )

    async def _renew_lease(
        self,
        *,
        token: str,
        lease_lost: asyncio.Event,
    ) -> None:
        while not lease_lost.is_set():
            await asyncio.sleep(self._config.heartbeat)
            try:
                renewed = await asyncio.to_thread(
                    self._cache.renew_lock_strict,
                    _LOCK_KEY,
                    token,
                    self._config.lock_ttl,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - outage means lease loss
                self._logger.warning(
                    "[todo][scheduler] lease renewal failed: %s",
                    exc,
                )
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return


__all__ = ["TodoScheduler"]
