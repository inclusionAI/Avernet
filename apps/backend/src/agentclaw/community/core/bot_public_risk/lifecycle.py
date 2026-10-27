"""Register queue handlers and recover due risk work across tenant scopes."""

import asyncio
import logging
from datetime import datetime
from typing import Callable

from agentclaw.community.core.bot_public_risk.notices import RiskNoticeTaskHandler
from agentclaw.community.core.bot_public_risk.tasks import (
    DETECTING,
    REJECT,
    SUBMIT,
    RiskTaskHandler,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

logger = logging.getLogger(__name__)


class RiskTaskLifecycle(LifecycleBase):
    def __init__(
        self,
        *,
        registry,
        tasks,
        delivery,
        repo,
        env_provider: Callable[[], str],
        worker_enabled: bool,
        clock: Callable[[], datetime],
        interval_seconds: int = 60,
    ):
        self.registry, self.tasks, self.delivery, self.repo = (
            registry,
            tasks,
            delivery,
            repo,
        )
        self.env_provider, self.worker_enabled, self.clock = (
            env_provider,
            worker_enabled,
            clock,
        )
        self.interval_seconds, self.loop_task = interval_seconds, None

    async def bootstrap(self):
        for kind in (SUBMIT, DETECTING, REJECT):
            self.registry.register(
                RiskTaskHandler(self.tasks, kind, self.env_provider),
                wake_on_enqueue=True,
            )
        self.registry.register(
            RiskNoticeTaskHandler(self.delivery, self.env_provider),
            wake_on_enqueue=True,
        )

    def reconcile_once(self):
        env, now = self.env_provider(), self.clock()
        for tenant in self.repo.list_recovery_tenants(env=env):
            try:
                with avernet_tenant_scope(tenant):
                    for recover in (self.tasks.recover, self.tasks.recover_notices):
                        cursor = ""
                        while True:
                            cursor = recover(env=env, now=now, after_id=cursor)
                            if cursor is None:
                                break
            except Exception:
                logger.exception(
                    "risk task recovery failed for tenant %s env %s", tenant, env
                )

    async def startup(self):
        if self.worker_enabled:
            self.loop_task = asyncio.create_task(self._loop())

    async def shutdown(self):
        if self.loop_task is not None:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
            self.loop_task = None

    async def _loop(self):
        while True:
            try:
                await asyncio.to_thread(self.reconcile_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("risk task recovery scan failed")
            await asyncio.sleep(self.interval_seconds)
