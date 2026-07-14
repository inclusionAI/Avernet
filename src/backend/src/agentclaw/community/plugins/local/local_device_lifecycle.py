"""LocalDeviceLifecycle — singlebox device boot/shutdown participant.

Split out of ``LocalDeviceAccessor`` in B9: the ``DeviceAccessor`` contract
(``get_connection_info`` / ``get_engine_config_path``) is neutral and lives in
``core/devices/services/local_device_accessor.py``, but the singlebox process
Lifecycle below is inherently local-runtime machinery — it drives other
``plugins/local`` components (process manager, device-sync symlinker, stale-
binding cleanup) — so it stays here in ``plugins/local`` (where importing
``plugins.local.*`` is allowed) as a standalone ``LifecycleBase`` participant.

- ``startup()``  releases stale bindings from a prior crash, re-allocates
  orphaned PENDING bots, and rebuilds the symlink tree under
  ``~/.openclaw/workspace/skills/`` from the DB's active skill sets.
- ``shutdown()`` stops every spawned adapter/openclaw process and releases all
  local device bindings so the next boot starts clean.

These hooks exist because local mode runs adapter + openclaw processes in-tree
(they die with the backend), unlike prod where Arca sandboxes survive backend
restarts and manage their own per-bot lifecycle.

Body lifted verbatim from the former ``LocalDevicePlugin`` lifecycle hooks.
B10 folds this into the local→baas device migration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.services.repositories import SkillSetRepository
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class LocalDeviceLifecycle(LifecycleBase):
    """Singlebox device boot/shutdown hooks (local-mode only)."""

    @inject
    def __init__(
        self,
        database: DatabasePlugin,
        bot_repository: BotRepository,
        skill_set_repo: SkillSetRepository,
        skill_set_factory_provider: Callable[[], SkillSetServiceFactory],
        bot_service_provider: Callable[[], BotService],
    ) -> None:
        # database: route lifecycle DB work through the locked local plugin.
        # bot_repository: resolve per-bot engine type during symlink restore.
        # skill_set_repo: walk active skill sets on startup.
        # skill_set_factory_provider / bot_service_provider: injected as lazy
        # ``Callable[[], …]`` because the eager type closes a construction cycle
        # through the skill-set service graph.
        self._database = database
        self._bot_repository = bot_repository
        self._skill_set_repo = skill_set_repo
        self._skill_set_factory_provider = skill_set_factory_provider
        self._bot_service_provider = bot_service_provider

    async def startup(self) -> None:
        """Lifecycle hook — clean prior-session state and rebuild symlinks.

        Three steps:
          1. ``release_all_stale_bindings()`` — safeguard against a crash
             that bypassed the shutdown hook.
          2. ``reallocate_orphaned_bots(BotService)`` — PENDING bots with
             no binding get fresh device allocation.
          3. ``_restore_local_symlinks()`` — full sync of active skill
             sets' symlink mappings to ``~/.openclaw/workspace/skills/``.

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
        await self._restore_local_symlinks()
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

    async def _restore_local_symlinks(self) -> None:
        """Rebuild ``~/.openclaw/workspace/skills/`` from active skill sets.

        Local environment runs a single shared OpenClaw process that reads
        symlinks from one directory. On boot, walk every active skill set,
        merge its mappings, and full-sync the result. The internal try/except
        around the per-skill-set loop is preserved so one bad skill set doesn't
        block the rest.
        """
        from agentclaw.community.core.bot_management.services.engine_resolver import (
            resolve_engine_for_bot,
        )
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            _get_bot_paths,
        )
        from agentclaw.community.plugins.local.device_sync import LocalDeviceSyncPlugin

        active_sets = self._skill_set_repo.get_all_active_skill_sets()
        if not active_sets:
            logger.info(
                "[restore_local_symlinks] No active skill sets found, skipping"
            )
            return

        logger.info(
            f"[restore_local_symlinks] Found {len(active_sets)} active skill sets"
        )

        # 合并所有 active skill sets 的 symlink mappings
        all_symlinks: list[dict[str, str]] = []
        seen_targets: set[str] = set()

        for skill_set in active_sets:
            skill_set_id = str(skill_set.get("id"))
            user_id = skill_set.get("user_id")
            bot_id = skill_set.get("bolt_id", "default")
            if not user_id:
                logger.warning(
                    f"[restore_local_symlinks] Skipping skill_set_id={skill_set_id}: missing user_id"
                )
                continue
            try:
                engine_type = resolve_engine_for_bot(
                    bot_id=bot_id,
                    owner_id=str(user_id),
                    bot_repo=self._bot_repository,
                )
                skills_dir, repo_dir, local_dir = _get_bot_paths(
                    user_id=str(user_id),
                    entity_id=str(user_id),
                    bot_id=bot_id,
                    engine_type=engine_type,
                )
                set_service = self._skill_set_factory_provider().create(
                    skills_dir=skills_dir,
                    repo_dir=repo_dir,
                    local_dir=local_dir,
                    user_id=str(user_id),
                    bot_id=bot_id,
                )
                mappings = set_service.get_symlink_mappings(
                    user_id=str(user_id),
                    bolt_id=bot_id,
                )
                for sm in mappings:
                    d = sm.to_dict()
                    target = d.get("target", "")
                    if target and target not in seen_targets:
                        seen_targets.add(target)
                        all_symlinks.append(d)
                logger.info(
                    f"[restore_local_symlinks] skill_set_id={skill_set_id} (bot={bot_id}): {len(mappings)} mappings"
                )
            except Exception as e:
                logger.error(
                    f"[restore_local_symlinks] Failed for skill_set_id={skill_set_id}: {e}"
                )

        # 一次性 full-sync 到 ~/.openclaw/workspace/skills/
        openclaw_skills_dir = Path.home() / ".openclaw" / "workspace" / "skills"
        plugin = LocalDeviceSyncPlugin(skills_dir=openclaw_skills_dir)
        result = plugin.sync_symlinks(all_symlinks)
        logger.info(f"[restore_local_symlinks] Done: {result}")
