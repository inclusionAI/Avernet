"""Unit tests for the local test-runtime lifecycle."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.plugins.local.local_device_lifecycle import LocalDeviceLifecycle


def _life(**over) -> LocalDeviceLifecycle:
    return LocalDeviceLifecycle(
        database=over.get("database", MagicMock()),
        bot_service_provider=over.get("bot_service_provider", lambda: MagicMock()),
    )


@pytest.mark.asyncio
async def test_startup_releases_and_reallocates() -> None:
    database = MagicMock()
    bot_service = MagicMock()
    life = _life(database=database, bot_service_provider=lambda: bot_service)
    with patch("agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings") as release, patch(
        "agentclaw.community.plugins.local.device_lifecycle.reallocate_orphaned_bots"
    ) as reallocate:
        await life.startup()
    release.assert_called_once_with(database)
    reallocate.assert_called_once_with(database, bot_service)


@pytest.mark.asyncio
async def test_shutdown_stops_processes_and_releases() -> None:
    database = MagicMock()
    life = _life(database=database)
    with patch("agentclaw.community.plugins.local.device_lifecycle.release_all_stale_bindings") as release, patch(
        "agentclaw.community.plugins.local.process_manager.LocalProcessManager"
    ) as process_manager:
        await life.shutdown()
    process_manager.instance.return_value.stop_all.assert_called_once_with()
    release.assert_called_once_with(database)
