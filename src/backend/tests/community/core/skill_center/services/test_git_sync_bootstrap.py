import asyncio
import logging
import os
import subprocess
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agentclaw.community.core.skill_center.services import git_sync as git_sync_module
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

    svc._cache_plugin.release_lock.assert_called_once()
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    assert result["success"] is False


# ==========================================================================
# Bootstrap repair path: serialized on the periodic sync's repo lock.
#
# Every worker process reaches this path on startup (the bare repo already
# exists, so the clone branch and its lock are skipped), so an unlocked fetch
# here means N workers fetch one bare repo at once and all but one die on
# "Unable to create ... shallow.lock: File exists".
# ==========================================================================


def _make_repair_service(tmp_path, *, lock_value="lock-1"):
    """A service whose skills subtree is missing, so the repair path runs."""
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc._repo_url = "https://example.test/aiworkbench.git"
    svc.config.bootstrap_wait_timeout = 6  # 测试用短超时
    target_dir = tmp_path / "skills-repo"
    svc.config.subtrees = [
        {
            "name": "skills",
            "target_dir": target_dir,
            "version_file": ".skills-version",
        }
    ]
    svc._cache_plugin = MagicMock()
    svc._cache_plugin.acquire_lock.return_value = lock_value

    async def _run_sync(func, *a, **k):
        return func(*a, **k)

    svc._run_sync = _run_sync
    return svc, target_dir


@pytest.mark.asyncio
async def test_repair_holds_repo_sync_lock_across_fetch(tmp_path):
    """The fetch runs under the lock, and it is the periodic sync's own key —
    a different key would not exclude a concurrent sync on the same repo."""
    svc, _ = _make_repair_service(tmp_path)
    observed = {}

    async def _fetch():
        observed["acquired_before_fetch"] = svc._cache_plugin.acquire_lock.call_count
        observed["released_before_fetch"] = svc._cache_plugin.release_lock.call_count
        return {"success": True}

    svc._git_fetch = _fetch
    svc._sync_subtree = AsyncMock(return_value={"success": True})

    result = await svc._ensure_skills_subtree_ready()

    assert result["method"] == "existing_repaired"
    assert observed == {"acquired_before_fetch": 1, "released_before_fetch": 0}
    lock_key = svc._cache_plugin.acquire_lock.call_args.args[0]
    assert lock_key.startswith("skill_repo_sync:")
    svc._cache_plugin.release_lock.assert_called_once_with(lock_key, "lock-1")


@pytest.mark.asyncio
async def test_repair_releases_lock_when_fetch_fails(tmp_path):
    svc, _ = _make_repair_service(tmp_path)
    svc._git_fetch = AsyncMock(return_value={"success": False, "error": "boom"})

    result = await svc._ensure_skills_subtree_ready()

    assert result["success"] is False
    assert result["method"] == "existing_repair_failed"
    assert "boom" in result["error"]
    svc._cache_plugin.release_lock.assert_called_once()


@pytest.mark.asyncio
async def test_repair_without_lock_waits_instead_of_fetching(tmp_path):
    """Not getting the lock must not mean fetching anyway — that is the bug."""
    svc, target_dir = _make_repair_service(tmp_path, lock_value=None)
    svc._git_fetch = AsyncMock()

    async def _sleep_then_peer_repairs(*_args, **_kwargs):
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / ".skills-version").write_text("deadbeef")
        await _real_sleep(0)

    with patch("asyncio.sleep", new=_sleep_then_peer_repairs):
        result = await svc._ensure_skills_subtree_ready()

    assert result == {"success": True, "method": "existing_repaired_by_peer"}
    svc._git_fetch.assert_not_awaited()
    svc._cache_plugin.release_lock.assert_not_called()


@pytest.mark.asyncio
async def test_repair_without_lock_times_out_without_fetching(tmp_path):
    svc, _ = _make_repair_service(tmp_path, lock_value=None)
    svc._git_fetch = AsyncMock()

    with patch("asyncio.sleep", new=_instant_sleep):
        result = await svc._ensure_skills_subtree_ready()

    assert result["success"] is False
    assert result["method"] == "existing_repair_failed"
    assert "6s" in result["error"]
    svc._git_fetch.assert_not_awaited()
    svc._cache_plugin.release_lock.assert_not_called()


@pytest.mark.asyncio
async def test_repair_skips_everything_when_subtree_already_present(tmp_path):
    svc, target_dir = _make_repair_service(tmp_path)
    target_dir.mkdir(parents=True)
    (target_dir / ".skills-version").write_text("deadbeef")
    svc._git_fetch = AsyncMock()

    result = await svc._ensure_skills_subtree_ready()

    assert result == {"success": True, "method": "existing"}
    svc._git_fetch.assert_not_awaited()
    svc._cache_plugin.acquire_lock.assert_not_called()


# ==========================================================================
# Bounded fetch + stale shallow.lock recovery
# ==========================================================================


def _make_fetch_service(tmp_path, *, timeout=300):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.config.git_fetch_timeout_seconds = timeout
    svc.config.remote_name = "origin"
    svc.config.branch = "master"
    bare = tmp_path / "aiworkbench.git"
    bare.mkdir(parents=True)
    svc.config.local_bare_repo = bare
    return svc, bare


def test_reaper_removes_shallow_lock_no_live_fetch_can_own(tmp_path):
    svc, bare = _make_fetch_service(tmp_path, timeout=300)
    lock = bare / "shallow.lock"
    lock.write_text("")
    stale = time.time() - (300 + 60 + 1)
    os.utime(lock, (stale, stale))

    svc._reap_stale_shallow_lock(bare)

    assert not lock.exists()


def test_reaper_keeps_lock_a_running_fetch_may_still_hold(tmp_path):
    svc, bare = _make_fetch_service(tmp_path, timeout=300)
    lock = bare / "shallow.lock"
    lock.write_text("")

    svc._reap_stale_shallow_lock(bare)

    assert lock.exists()


def test_reaper_is_a_noop_without_a_lock_file(tmp_path):
    svc, bare = _make_fetch_service(tmp_path)

    svc._reap_stale_shallow_lock(bare)  # must not raise

    assert not (bare / "shallow.lock").exists()


def test_sync_git_fetch_bounds_the_subprocess(tmp_path):
    svc, _ = _make_fetch_service(tmp_path, timeout=11)
    completed = MagicMock(returncode=0, stderr="")

    with patch.object(git_sync_module.subprocess, "run", return_value=completed) as run:
        result = svc._sync_git_fetch()

    assert result["success"] is True
    assert run.call_args.kwargs["timeout"] == 11


def test_sync_git_fetch_reports_timeout_instead_of_raising(tmp_path):
    svc, _ = _make_fetch_service(tmp_path, timeout=7)
    boom = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=7)

    with patch.object(git_sync_module.subprocess, "run", side_effect=boom):
        result = svc._sync_git_fetch()

    assert result["success"] is False
    assert "timed out after 7s" in result["error"]


def test_sync_git_fetch_reaps_stale_lock_before_fetching(tmp_path):
    """A repo poisoned by an earlier killed fetch recovers on the next cycle."""
    svc, bare = _make_fetch_service(tmp_path, timeout=300)
    lock = bare / "shallow.lock"
    lock.write_text("")
    stale = time.time() - (300 + 60 + 1)
    os.utime(lock, (stale, stale))
    completed = MagicMock(returncode=0, stderr="")

    with patch.object(git_sync_module.subprocess, "run", return_value=completed):
        result = svc._sync_git_fetch()

    assert result["success"] is True
    assert not lock.exists()
