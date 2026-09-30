import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services import git_sync as git_sync_module


def _make_service():
    svc = GitSyncService.__new__(GitSyncService)
    oss_dir = MagicMock()  # oss_dir.mkdir 是 MagicMock 调用，无需 patch
    svc.config = MagicMock()
    svc.config.subtrees = [{"name": "skills", "target_dir": "/local/skills", "oss_dir": oss_dir}]
    return svc


def _run_with_rc(returncode, stderr="", caplog=None):
    svc = _make_service()
    completed = MagicMock(returncode=returncode, stderr=stderr)
    # The module logger may be a non-propagating SOFAPy logger (propagate=False)
    # when sofapy_base is installed, so caplog's root handler would miss it.
    # Attach caplog's handler directly to the module logger.
    if caplog is not None:
        git_sync_module.logger.addHandler(caplog.handler)
    try:
        with patch("subprocess.run", return_value=completed):
            svc._sync_to_oss_sync()
    finally:
        if caplog is not None:
            git_sync_module.logger.removeHandler(caplog.handler)


def test_rsync_exit_24_treated_as_acceptable(caplog):
    with caplog.at_level(logging.WARNING):
        _run_with_rc(24, "file has vanished: .fuse_hidden0001", caplog=caplog)
    assert not any("OSS sync failed" in r.getMessage() for r in caplog.records)


def test_rsync_exit_other_nonzero_is_failure(caplog):
    with caplog.at_level(logging.WARNING):
        _run_with_rc(23, "some real error", caplog=caplog)
    assert any("OSS sync failed" in r.getMessage() for r in caplog.records)


def test_rsync_command_excludes_fuse_hidden():
    svc = _make_service()
    completed = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=completed) as m:
        svc._sync_to_oss_sync()
    args = m.call_args[0][0]
    assert "--exclude=.fuse_hidden*" in args


@pytest.mark.asyncio
async def test_sync_to_oss_async_runs_sync_via_run_sync():
    """_sync_to_oss_async 成功路径：把 _sync_to_oss_sync 经 _run_sync 跑掉。"""
    svc = _make_service()
    svc._sync_to_oss_sync = MagicMock()
    svc._run_sync = AsyncMock()
    await svc._sync_to_oss_async()
    svc._run_sync.assert_awaited_once_with(svc._sync_to_oss_sync)


@pytest.mark.asyncio
async def test_sync_to_oss_async_swallows_exception(caplog):
    """_sync_to_oss_async 失败时只 logger.warning，不向上抛。"""
    svc = _make_service()
    svc._run_sync = AsyncMock(side_effect=RuntimeError("boom"))
    git_sync_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING):
            await svc._sync_to_oss_async()  # 不应该抛
    finally:
        git_sync_module.logger.removeHandler(caplog.handler)
    assert any("OSS sync failed" in r.getMessage() for r in caplog.records)
