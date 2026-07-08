"""Unit tests for LocalDeviceLifecycle (B9 — split out of LocalDeviceAccessor).

The singlebox device boot/shutdown participant: startup releases stale bindings,
reallocates orphaned bots, and rebuilds the skills symlink tree; shutdown stops
spawned processes and releases bindings. These bodies were previously untested;
covered here directly (the discovery test only proves the participant is found).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.plugins.local.local_device_lifecycle import LocalDeviceLifecycle


def _life(**over) -> LocalDeviceLifecycle:
    return LocalDeviceLifecycle(
        bot_repository=over.get("bot_repository", MagicMock()),
        skill_set_repo=over.get("skill_set_repo", MagicMock()),
        skill_set_factory_provider=over.get(
            "skill_set_factory_provider", lambda: MagicMock()
        ),
        bot_service_provider=over.get("bot_service_provider", lambda: MagicMock()),
    )


@pytest.mark.asyncio
async def test_startup_runs_release_reallocate_restore():
    life = _life()
    with patch(
        "agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings"
    ) as rel, patch(
        "agentclaw.community.plugins.local.device_lifecycle.reallocate_orphaned_bots"
    ) as realloc, patch.object(
        life, "_restore_local_symlinks", new=AsyncMock()
    ) as restore:
        await life.startup()

    rel.assert_called_once_with()
    realloc.assert_called_once()
    restore.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_shutdown_stops_processes_and_releases():
    life = _life()
    with patch(
        "agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings"
    ) as rel, patch(
        "agentclaw.community.plugins.local.process_manager.LocalProcessManager"
    ) as pm:
        await life.shutdown()

    pm.instance.return_value.stop_all.assert_called_once_with()
    rel.assert_called_once_with()


@pytest.mark.asyncio
async def test_restore_symlinks_no_active_sets_is_noop():
    ssr = MagicMock()
    ssr.get_all_active_skill_sets.return_value = []
    life = _life(skill_set_repo=ssr)

    with patch(
        "agentclaw.community.plugins.local.device_sync.LocalDeviceSyncPlugin"
    ) as sync_cls:
        await life._restore_local_symlinks()

    sync_cls.assert_not_called()


@pytest.mark.asyncio
async def test_restore_symlinks_collects_mappings_and_full_syncs():
    ssr = MagicMock()
    ssr.get_all_active_skill_sets.return_value = [
        {"id": 1, "user_id": "u1", "bolt_id": "b1"},
        {"id": 2, "user_id": None},  # skipped: missing user_id
    ]
    mapping = MagicMock()
    mapping.to_dict.return_value = {"target": "/t", "source": "/s"}
    set_service = MagicMock()
    set_service.get_symlink_mappings.return_value = [mapping]
    factory = MagicMock()
    factory.create.return_value = set_service
    life = _life(skill_set_repo=ssr, skill_set_factory_provider=lambda: factory)

    with patch(
        "agentclaw.community.core.bot_management.services.engine_resolver.resolve_engine_for_bot",
        return_value="openclaw",
    ), patch(
        "agentclaw.community.core.skill_center.services.skill_set_service._get_bot_paths",
        return_value=("/skills", "/repo", "/local"),
    ), patch(
        "agentclaw.community.plugins.local.device_sync.LocalDeviceSyncPlugin"
    ) as sync_cls:
        sync_cls.return_value.sync_symlinks.return_value = {"synced": 1}
        await life._restore_local_symlinks()

    sync_cls.return_value.sync_symlinks.assert_called_once_with(
        [{"target": "/t", "source": "/s"}]
    )


@pytest.mark.asyncio
async def test_restore_symlinks_one_bad_set_does_not_block_rest():
    ssr = MagicMock()
    ssr.get_all_active_skill_sets.return_value = [
        {"id": 1, "user_id": "u1", "bolt_id": "b1"},
    ]
    life = _life(skill_set_repo=ssr)

    with patch(
        "agentclaw.community.core.bot_management.services.engine_resolver.resolve_engine_for_bot",
        side_effect=RuntimeError("resolve boom"),
    ), patch(
        "agentclaw.community.plugins.local.device_sync.LocalDeviceSyncPlugin"
    ) as sync_cls:
        sync_cls.return_value.sync_symlinks.return_value = {"synced": 0}
        await life._restore_local_symlinks()

    # The per-set error is swallowed; sync still runs with an empty mapping list.
    sync_cls.return_value.sync_symlinks.assert_called_once_with([])
