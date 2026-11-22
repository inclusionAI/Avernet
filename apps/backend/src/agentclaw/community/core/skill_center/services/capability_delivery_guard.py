"""Serialize capability delivery by tenant, environment and stable Bot address."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError, CachePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()
_TTL_SECONDS = 90
_WAIT_SECONDS = 5


class CapabilityDeliveryUnavailable(RuntimeError):
    """Another process owns the delivery boundary or its lease was lost."""


@dataclass
class CapabilityDeliveryLease:
    cache: CachePlugin
    key: str
    token: str
    lost: bool = False

    async def verify(self) -> None:
        await asyncio.to_thread(self.verify_from_worker)

    def verify_from_worker(self) -> None:
        if self.lost:
            raise CapabilityDeliveryUnavailable()
        try:
            owned = self.cache.renew_lock_strict(self.key, self.token, ttl=_TTL_SECONDS)
        except CacheLockInfrastructureError as exc:
            self.lost = True
            raise CapabilityDeliveryUnavailable() from exc
        if not owned:
            self.lost = True
            raise CapabilityDeliveryUnavailable()

    async def keep_alive(self) -> None:
        while True:
            await asyncio.sleep(_TTL_SECONDS / 3)
            try:
                await self.verify()
            except CapabilityDeliveryUnavailable:
                logger.warning("Capability delivery lease renewal failed")
                return


class CapabilityDeliveryGuard:
    def __init__(self, cache: CachePlugin) -> None:
        self._cache = cache
        self._held: ContextVar[CapabilityDeliveryLease | None] = ContextVar(
            "capability_delivery_lease", default=None
        )

    @asynccontextmanager
    async def hold(self, *, bot_id: str, entity_id: str) -> AsyncIterator[CapabilityDeliveryLease]:
        tenant = get_current_avernet_tenant()
        env = get_current_env()
        key = f"capability-delivery:{tenant}:{env}:{len(entity_id)}:{entity_id}:{bot_id}"
        held = self._held.get()
        if held is not None and held.key == key:
            await held.verify()
            yield held
            return
        clock = asyncio.get_running_loop()
        deadline = clock.time() + _WAIT_SECONDS
        while True:
            try:
                token = await asyncio.to_thread(self._cache.acquire_lock_strict, key, _TTL_SECONDS)
            except CacheLockInfrastructureError as exc:
                raise CapabilityDeliveryUnavailable() from exc
            if token is not None:
                break
            if clock.time() >= deadline:
                raise CapabilityDeliveryUnavailable()
            await asyncio.sleep(0.05)
        lease = CapabilityDeliveryLease(self._cache, key, token)
        context_token = self._held.set(lease)
        heartbeat = asyncio.create_task(lease.keep_alive())
        try:
            yield lease
        finally:
            self._held.reset(context_token)
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            try:
                await asyncio.to_thread(self._cache.release_lock, key, token)
            except CacheLockInfrastructureError:
                logger.warning("Capability delivery lease release failed")
