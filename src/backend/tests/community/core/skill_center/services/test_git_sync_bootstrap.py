import asyncio
import logging
import subprocess
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService


def _make_service(repo_exists_sequence):
    """repo_exists_sequence: list[bool] 依次作为 local_bare_repo.exists() 返回值。"""
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    # repo_url is a runtime secret resolved in __init__ (bypassed by __new__);
    # set it so sync_bootstrap() passes the "no git source configured" guard.
    svc._repo_url = "https://example.test/aiworkbench.git"
    svc.config.bootstrap_wait_timeout = 6  # 测试用短超时
    svc.config.local_bare_repo.exists.side_effect = list(repo_exists_sequence)
    svc._cache_plugin = MagicMock()
    svc._cache_plugin.acquire_lock.return_value = None  # 抢不到锁

    async def _run_sync(func, *a, **k):
        return func(*a, **k)

    svc._run_sync = _run_sync
    return svc


# Capture the real sleep before patching so the no-op fast-forward does not
# recurse into the patched name.
_real_sleep = asyncio.sleep


async def _instant_sleep(*_args, **_kwargs):
    await _real_sleep(0)


@pytest.mark.asyncio
async def test_bootstrap_no_lock_waits_until_repo_ready():
    svc = _make_service([False, False, True])  # 入口检查 + 等待循环
    with patch("asyncio.sleep", new=_instant_sleep):
        result = await svc.sync_bootstrap()
    assert result["success"] is True
    assert result["method"] == "existing"


@pytest.mark.asyncio
async def test_bootstrap_no_lock_times_out_returns_failed():
    svc = _make_service([False] + [False] * 10)
    with patch("asyncio.sleep", new=_instant_sleep):
        result = await svc.sync_bootstrap()
    assert result["success"] is False
    assert result["method"] == "wait_timeout"


@pytest.mark.asyncio
async def test_bootstrap_clone_failure_logs_error_and_releases_lock(caplog):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc._repo_url = "https://example.test/aiworkbench.git"
    svc.config.local_bare_repo.exists.return_value = False
    svc.config.bootstrap_wait_timeout = 60
    svc._cache_plugin = MagicMock()
    svc._cache_plugin.acquire_lock.return_value = "lock-token"  # 抢到锁

    async def _run_sync(func, *a, **k):
        return func(*a, **k)

    svc._run_sync = _run_sync
    svc._clone_bare_repo = AsyncMock(side_effect=RuntimeError("clone boom"))
    svc._download_from_oss_and_extract = AsyncMock(side_effect=RuntimeError("oss boom"))

    # The module logger may be a non-propagating SOFAPy logger (propagate=False)
    # when sofapy_base is installed, so caplog's root handler would miss it.
    # Attach caplog's handler directly to the module logger to capture records
    # regardless of environment.
    from agentclaw.community.core.skill_center.services import git_sync as git_sync_module

    git_sync_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.ERROR):
            result = await svc.sync_bootstrap()
    finally:
        git_sync_module.logger.removeHandler(caplog.handler)

    svc._download_from_oss_and_extract.assert_awaited_once()
    svc._cache_plugin.release_lock.assert_called_once()
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert result["success"] is False


def test_clone_timeout_is_bounded_and_cleans_partial_repo(tmp_path):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.config.local_bare_repo = tmp_path / "aiworkbench.git"
    svc.config.branch = "master"
    svc.config.clone_timeout_seconds = 20
    svc._repo_url = "ssh://git@example.test/aiworkbench.git"

    def _hang_until_timeout(*_args, **kwargs):
        svc.config.local_bare_repo.mkdir()
        raise subprocess.TimeoutExpired(
            cmd="git clone",
            timeout=kwargs.get("timeout"),
        )

    with patch(
        "agentclaw.community.core.skill_center.services.git_sync.subprocess.run",
        side_effect=_hang_until_timeout,
    ) as run:
        with pytest.raises(subprocess.TimeoutExpired):
            svc._sync_clone_bare_repo()

    assert run.call_args.kwargs["timeout"] == 20
    assert not svc.config.local_bare_repo.exists()


def test_oss_fallback_does_not_extract_over_partial_repo(tmp_path):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.config.local_bare_repo = tmp_path / "aiworkbench.git"
    svc.config.local_bare_repo.mkdir()
    svc.config.oss_archive_path = "archives/aiworkbench.tar.gz"
    svc._oss_storage = MagicMock()

    with (
        patch(
            "agentclaw.community.core.skill_center.services.git_sync.requests.get",
        ) as download,
        patch(
            "agentclaw.community.core.skill_center.services.git_sync.subprocess.run"
        ) as tar,
    ):
        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            svc._sync_download_from_oss_and_extract()

    svc._oss_storage.sign_url.assert_not_called()
    download.assert_not_called()
    tar.assert_not_called()


@pytest.mark.asyncio
async def test_post_clone_failure_preserves_valid_bare_repo(tmp_path):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.config.local_bare_repo = tmp_path / "aiworkbench.git"
    svc.config.enable_oss_sync = True
    svc.config.subtrees = [{"name": "skills"}]
    svc._oss_storage = MagicMock()

    async def _clone():
        svc.config.local_bare_repo.mkdir()

    async def _run_sync(_func, *_args, **_kwargs):
        raise RuntimeError("OSS upload failed")

    async def _fallback():
        svc._sync_download_from_oss_and_extract()

    svc._clone_bare_repo = AsyncMock(side_effect=_clone)
    svc._git_fetch = AsyncMock(return_value={"success": True})
    svc._sync_subtree = AsyncMock(return_value={"success": True})
    svc._run_sync = _run_sync
    svc._download_from_oss_and_extract = AsyncMock(side_effect=_fallback)

    result = await svc._bootstrap_clone_or_fallback()

    assert result["success"] is False
    assert svc.config.local_bare_repo.exists()
    svc._download_from_oss_and_extract.assert_awaited_once()
    svc._oss_storage.sign_url.assert_not_called()
