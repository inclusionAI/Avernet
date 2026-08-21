"""Lifecycle hooks for the local test runtime.

The participant releases stale bindings, reallocates orphaned local-test bots,
and stops spawned processes. Production Singlebox uses the BaaS device runtime
and does not bind this lifecycle.
"""
from __future__ import annotations

from typing import Callable

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class LocalDeviceLifecycle(LifecycleBase):
    """Boot/shutdown hooks for the local test runtime."""

    @inject
    def __init__(
        self,
        database: DatabasePlugin,
        bot_service_provider: Callable[[], BotService],
    ) -> None:
        # Keep BotService lazy because the eager dependency closes a DI cycle.
        self._database = database
        self._bot_service_provider = bot_service_provider

    async def startup(self) -> None:
        """Lifecycle hook — clean prior-session state before local test execution.

        Steps:
          1. ``release_all_stale_bindings()`` — safeguard against a crash
             that bypassed the shutdown hook.
          2. ``reallocate_orphaned_bots(BotService)`` — PENDING bots with
             no binding get fresh device allocation.

        Errors propagate (fail-fast boot).
        """
        from agentclaw.community.plugins.local.device_lifecycle import (
            reallocate_orphaned_bots,
            release_all_stale_bindings,
        )

        release_all_stale_bindings(self._database)
        reallocate_orphaned_bots(
            self._database,
            self._bot_service_provider(),
        )
        logger.info("LocalDeviceLifecycle started via Lifecycle.startup()")

    async def shutdown(self) -> None:
        """Lifecycle hook — stop spawned processes and release bindings."""
        from agentclaw.community.plugins.local.device_lifecycle import (
            release_all_stale_bindings,
        )
        from agentclaw.community.plugins.local.process_manager import (
            LocalProcessManager,
        )

        LocalProcessManager.instance().stop_all()
        release_all_stale_bindings(self._database)
        logger.info("LocalDeviceLifecycle shut down via Lifecycle.shutdown()")
