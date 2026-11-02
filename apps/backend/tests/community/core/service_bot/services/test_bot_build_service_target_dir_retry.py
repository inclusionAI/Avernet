"""BotBuildService target directory availability behavior."""

from pathlib import Path
from unittest.mock import MagicMock

import errno
import pytest

from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)


@pytest.mark.unit
def test_target_dir_retry_succeeds_after_enotconn(tmp_path, monkeypatch):
    target_dir = tmp_path / "target"
    mkdir = MagicMock()
    mkdir.side_effect = [
        OSError(errno.ENOTCONN, "Transport endpoint is not connected"),
        None,
    ]
    monkeypatch.setattr(Path, "mkdir", mkdir)

    service = BotBuildService.__new__(BotBuildService)
    service._run_local_command = MagicMock()
    assert service._ensure_target_dir_ready(target_dir) is None
    assert mkdir.call_count == 2
    mkdir.assert_called_with(parents=True, exist_ok=True)


@pytest.mark.unit
def test_target_dir_retry_after_enotconn_raises_non_retryable(tmp_path, monkeypatch):
    target_dir = tmp_path / "target"
    mkdir = MagicMock(side_effect=OSError(errno.EACCES, "Permission denied"))
    monkeypatch.setattr(Path, "mkdir", mkdir)

    service = BotBuildService.__new__(BotBuildService)
    service._migrate_bot_instance = MagicMock()
    with pytest.raises(OSError, match="Permission denied"):
        service._ensure_target_dir_ready(target_dir)
    service._migrate_bot_instance.assert_not_called()


@pytest.mark.unit
def test_target_dir_retry_exhaustion_raises(tmp_path, monkeypatch):
    target_dir = tmp_path / "target"
    mkdir = MagicMock(
        side_effect=OSError(errno.ENOTCONN, "Transport endpoint is not connected")
    )
    monkeypatch.setattr(Path, "mkdir", mkdir)
    sleep = MagicMock()
    monkeypatch.setattr(module.time, "sleep", sleep)

    service = BotBuildService.__new__(BotBuildService)
    service._migrate_bot_instance = MagicMock()
    with pytest.raises(OSError, match="Transport endpoint is not connected"):
        service._ensure_target_dir_ready(target_dir)
    assert sleep.call_count == module._TARGET_DIR_MIGRATION_MAX_ATTEMPTS - 1
