"""Shared periodic-loop machinery for backend-owned BBS tasks."""

from __future__ import annotations

import asyncio
import random
from typing import Any

from agentclaw.community.core.task.forum.shared.protocols import BbsTaskExecutorProtocol
from agentclaw.community.core.task.forum.shared.task.sandbox import BbsSandboxTask
from agentclaw.community.di.bbs_digest_config import BbsTaskConfig
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin


class BbsPeriodicTaskScheduler(LifecycleBase):
    """Periodically execute one named task while holding a named lease.

    Every backend process may start this lifecycle participant. On each tick,
    the process attempts to acquire one cache lease through ``CachePlugin``.
    Exactly one process can hold that lease at a time; the others return a
    lock-held result and wait for the next tick.
    """

    def __init__(
        self,
        *,
        executor: BbsTaskExecutorProtocol,
        cache: CachePlugin,
        config: BbsTaskConfig,
        task: BbsSandboxTask,
        lock_key: str,
        label: str,
    ) -> None:
        self._executor = executor
        self._cache = cache
        self._config = config
        self._task = task
        self._lock_key = lock_key
        self._label = label
        self._logger = get_logger()
        self._started = False
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def lock_key(self) -> str:
        return self._lock_key

    async def startup(self) -> None:
        if self._started:
            return
        if not self._config.enabled:
            self._logger.info("[%s][scheduler] disabled by configuration", self._label)
            return

        self._started = True
        self._loop_task = asyncio.create_task(self._loop())
        self._logger.info(
            "[%s][scheduler] started interval=%ss lock=%s",
            self._label,
            self._config.interval,
            self._lock_key,
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
        self._logger.info("[%s][scheduler] stopped", self._label)

    async def _loop(self) -> None:
        while self._started:
            jitter = random.uniform(0, self._config.jitter)
            await asyncio.sleep(self._config.interval + jitter)
            if not self._started:
                return
            try:
                result = await self.run_once()
                self._logger.info("[%s][scheduler] tick result=%s", self._label, result)
            except Exception as exc:  # noqa: BLE001 — one bad tick must not kill the loop
                self._logger.error("[%s][scheduler] tick failed: %s", self._label, exc)

    async def run_once(self) -> dict[str, Any]:
        """Run one tick without asserting that the periodic loop is active.

        This visible method makes the scheduling boundary testable without real
        clock waits. In a deployment ticks come from :meth:`_loop`; the method
        is also deterministic and safe when called by an operator.
        """
        token = await asyncio.to_thread(
            self._cache.acquire_lock_strict,
            self._lock_key,
            self._config.lock_ttl,
        )
        if token is None:
            return {
                "success": False,
                "status": "lock_held",
                "lock_key": self._lock_key,
            }

        try:
            result = await self._execute_with_lease_guard(lease_token=token)
            return {"status": "executed", **result}
        finally:
            released = await asyncio.to_thread(
                self._cache.release_lock,
                self._lock_key,
                token,
            )
            if not released:
                self._logger.warning(
                    "[%s][scheduler] lease release failed or expired: %s",
                    self._label,
                    self._lock_key,
                )

    async def _execute_with_lease_guard(self, *, lease_token: str) -> dict[str, Any]:
        lock_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._renew_lease_loop(lease_token=lease_token, lock_lost=lock_lost)
        )
        execution = asyncio.create_task(self._executor.execute_once(self._task))
        waiter = asyncio.create_task(lock_lost.wait())

        try:
            done, _pending = await asyncio.wait(
                {execution, waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            heartbeat.cancel()
            waiter.cancel()
            execution.cancel()
            await asyncio.gather(heartbeat, waiter, execution, return_exceptions=True)
            raise

        if execution in done:
            await self._cancel_tasks(heartbeat, waiter)
            error = execution.exception()
            if error is not None:
                raise error
            return dict(execution.result())

        await self._cancel_tasks(heartbeat, execution)
        return {
            "success": False,
            "status": "lease_lost",
            "lock_key": self._lock_key,
        }

    async def _renew_lease_loop(
        self,
        *,
        lease_token: str,
        lock_lost: asyncio.Event,
    ) -> None:
        while not lock_lost.is_set():
            await asyncio.sleep(self._config.heartbeat)
            try:
                renewed = await asyncio.to_thread(
                    self._cache.renew_lock_strict,
                    self._lock_key,
                    lease_token,
                    self._config.lock_ttl,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    "[%s][scheduler] lease renewal failed: %s",
                    self._label,
                    exc,
                )
                lock_lost.set()
                return
            if not renewed:
                lock_lost.set()
                return

    @staticmethod
    async def _cancel_tasks(*tasks: asyncio.Task[Any]) -> None:
        await asyncio.gather(
            *(_cancel_and_wait(task) for task in tasks),
            return_exceptions=True,
        )


async def _cancel_and_wait(task: asyncio.Task[Any]) -> None:
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


__all__ = ["BbsPeriodicTaskScheduler"]
