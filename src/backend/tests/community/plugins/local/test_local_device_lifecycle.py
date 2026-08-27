"""Unit tests for the local test-runtime lifecycle."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.plugins.local.local_device_lifecycle import LocalDeviceLifecycle


def _life(**over) -> LocalDeviceLifecycle:
    return LocalDeviceLifecycle(
        database=over.get("database", MagicMock()),
        bot_repository=over.get("bot_repository", MagicMock()),
        skill_set_repo=over.get("skill_set_repo", MagicMock()),
        skill_set_factory_provider=over.get(
            "skill_set_factory_provider", lambda: MagicMock()
        ),
        bot_service_provider=over.get("bot_service_provider", lambda: MagicMock()),
        symlink_synchronizer=over.get("symlink_synchronizer", MagicMock()),
    )


@pytest.mark.asyncio
async def test_startup_releases_reallocates_and_restores() -> None:
    database = MagicMock()
    bot_service = MagicMock()
    life = _life(database=database, bot_service_provider=lambda: bot_service)
    with patch(
        "agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings"
    ) as release, patch(
        "agentclaw.community.plugins.local.device_lifecycle.reallocate_orphaned_bots"
    ) as reallocate, patch.object(
        life, "_restore_local_symlinks", new=AsyncMock()
    ) as restore:
        await life.startup()

    release.assert_called_once_with(database)
    reallocate.assert_called_once_with(database, bot_service)
    restore.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_stops_processes_and_releases() -> None:
    database = MagicMock()
    life = _life(database=database)
    with patch(
        "agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings"
    ) as release, patch(
        "agentclaw.community.plugins.local.process_manager.LocalProcessManager"
    ) as process_manager:
        await life.shutdown()

    process_manager.instance.return_value.stop_all.assert_called_once_with()
    release.assert_called_once_with(database)


def test_database_dependency_is_required() -> None:
    with pytest.raises(TypeError, match="database"):
        LocalDeviceLifecycle(
            bot_repository=MagicMock(),
            skill_set_repo=MagicMock(),
            skill_set_factory_provider=lambda: MagicMock(),
            bot_service_provider=lambda: MagicMock(),
            symlink_synchronizer=MagicMock(),
        )


@pytest.mark.asyncio
async def test_restore_symlinks_no_active_sets_is_noop() -> None:
    skill_set_repo = MagicMock()
    skill_set_repo.get_all_active_skill_sets.return_value = []
    synchronizer = MagicMock()
    life = _life(skill_set_repo=skill_set_repo, symlink_synchronizer=synchronizer)

    await life._restore_local_symlinks()

    synchronizer.sync.assert_not_called()


@pytest.mark.asyncio
async def test_restore_symlinks_collects_mappings_and_full_syncs() -> None:
    skill_set_repo = MagicMock()
    skill_set_repo.get_all_active_skill_sets.return_value = [
        {"id": 1, "user_id": "u1", "bolt_id": "b1"},
        {"id": 2, "user_id": None},
    ]
    mapping = MagicMock()
    mapping.to_dict.return_value = {"target": "/t", "source": "/s"}
    set_service = MagicMock()
    set_service.get_symlink_mappings.return_value = [mapping]
    factory = MagicMock()
    factory.create.return_value = set_service
    synchronizer = MagicMock()
    life = _life(
        skill_set_repo=skill_set_repo,
        skill_set_factory_provider=lambda: factory,
        symlink_synchronizer=synchronizer,
    )

    with patch(
        "agentclaw.community.core.bot_management.services.engine_resolver.resolve_engine_for_bot",
        return_value="openclaw",
    ), patch(
        "agentclaw.community.core.bot_management.services.engine_resolver.resolve_runtime_engine_for_bot",
        return_value="openclaw",
    ):
        await life._restore_local_symlinks()

    synchronizer.sync.assert_called_once_with([{"target": "/t", "source": "/s"}])


@pytest.mark.asyncio
async def test_restore_symlinks_one_bad_set_does_not_block_sync() -> None:
    skill_set_repo = MagicMock()
    skill_set_repo.get_all_active_skill_sets.return_value = [
        {"id": 1, "user_id": "u1", "bolt_id": "b1"},
    ]
    synchronizer = MagicMock()
    life = _life(skill_set_repo=skill_set_repo, symlink_synchronizer=synchronizer)

    with patch(
        "agentclaw.community.core.bot_management.services.engine_resolver.resolve_engine_for_bot",
        side_effect=RuntimeError("resolve boom"),
    ):
        await life._restore_local_symlinks()

    synchronizer.sync.assert_called_once_with([])
