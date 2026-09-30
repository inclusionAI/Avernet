"""Lifecycle hooks for the local test runtime.

The participant releases stale bindings, reallocates orphaned local-test bots,
restores their host skill symlinks, and stops spawned processes. Production
Singlebox uses the BaaS device runtime and does not bind this lifecycle.
"""
from __future__ import annotations

from typing import Callable

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local.skill_symlink_sync import (
    LocalSkillSymlinkSynchronizer,
)

logger = get_logger()


class LocalDeviceLifecycle(LifecycleBase):
    """Boot/shutdown hooks for the local test runtime."""

    @inject
    def __init__(
        self,
        database: DatabasePlugin,
        bot_repository: BotRepository,
        skill_set_repo: SkillSetRepository,
        skill_set_factory_provider: Callable[[], SkillSetServiceFactory],
        bot_service_provider: Callable[[], BotService],
        symlink_synchronizer: LocalSkillSymlinkSynchronizer,
    ) -> None:
        self._database = database
        self._bot_repository = bot_repository
        self._skill_set_repo = skill_set_repo
        self._skill_set_factory_provider = skill_set_factory_provider
        self._bot_service_provider = bot_service_provider
        self._symlink_synchronizer = symlink_synchronizer

    async def startup(self) -> None:
        """Clean prior-session state, reallocate bots, and restore skill links."""
        from agentclaw.community.plugins.local.device_lifecycle import (
            reallocate_orphaned_bots,
            release_all_stale_bindings,
        )

        release_all_stale_bindings(self._database)
        reallocate_orphaned_bots(
            self._database,
            self._bot_service_provider(),
        )
        await self._restore_local_symlinks()
        logger.info("LocalDeviceLifecycle started via Lifecycle.startup()")

    async def shutdown(self) -> None:
        """Stop spawned processes and release bindings."""
        from agentclaw.community.plugins.local.device_lifecycle import (
            release_all_stale_bindings,
        )
        from agentclaw.community.plugins.local.process_manager import (
            LocalProcessManager,
        )

        LocalProcessManager.instance().stop_all()
        release_all_stale_bindings(self._database)
        logger.info("LocalDeviceLifecycle shut down via Lifecycle.shutdown()")

    async def _restore_local_symlinks(self) -> None:
        """Rebuild local test-runtime skill links from active skill sets."""
        from agentclaw.community.core.bot_management.services.engine_resolver import (
            resolve_engine_for_bot,
            resolve_runtime_engine_for_bot,
        )

        active_sets = self._skill_set_repo.get_all_active_skill_sets()
        if not active_sets:
            logger.info("[restore_local_symlinks] No active skill sets found, skipping")
            return

        all_symlinks: list[dict[str, str]] = []
        seen_targets: set[str] = set()
        for skill_set in active_sets:
            skill_set_id = str(skill_set.get("id"))
            user_id = skill_set.get("user_id")
            bot_id = skill_set.get("bolt_id", "default")
            if not user_id:
                logger.warning(
                    "[restore_local_symlinks] Skipping skill_set_id=%s: missing user_id",
                    skill_set_id,
                )
                continue
            try:
                engine_type = skill_set.get("engine_type") or resolve_engine_for_bot(
                    bot_id=bot_id,
                    owner_id=str(user_id),
                    bot_repo=self._bot_repository,
                )
                runtime_engine_type = resolve_runtime_engine_for_bot(
                    bot_id=bot_id,
                    owner_id=str(user_id),
                    bot_repo=self._bot_repository,
                )
                set_service = self._skill_set_factory_provider().create(
                    user_id=str(user_id),
                    entity_id=str(user_id),
                    bot_id=bot_id,
                    engine_type=str(engine_type),
                    runtime_engine_type=runtime_engine_type,
                )
                mappings = set_service.get_symlink_mappings(
                    user_id=str(user_id),
                    bolt_id=bot_id,
                )
                for mapping in mappings:
                    item = mapping.to_dict()
                    target = item.get("target", "")
                    if target and target not in seen_targets:
                        seen_targets.add(target)
                        all_symlinks.append(item)
            except Exception as exc:
                logger.error(
                    "[restore_local_symlinks] Failed for skill_set_id=%s: %s",
                    skill_set_id,
                    exc,
                )

        result = self._symlink_synchronizer.sync(all_symlinks)
        logger.info("[restore_local_symlinks] Done: %s", result)
