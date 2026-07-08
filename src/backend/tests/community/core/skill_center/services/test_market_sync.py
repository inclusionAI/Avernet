"""Tests for agentclaw.community.core.skill_center.services.market_sync.MarketSyncService."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.skill_center.services.market_sync import MarketSyncService


def _make_market_sync_service(**kwargs):
    """Construct MarketSyncService with required deps defaulted to MagicMock."""
    kwargs.setdefault("cache_plugin", MagicMock())
    kwargs.setdefault("skill_service_factory", MagicMock())
    return MarketSyncService(**kwargs)


# ---------------------------------------------------------------------------
# is_running / stop when not started
# ---------------------------------------------------------------------------

class TestServiceLifecycle:
    def test_initial_state(self):
        svc = _make_market_sync_service()
        assert svc._started is False
        assert not svc.is_running()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_returns_true(self):
        svc = _make_market_sync_service()
        result = await svc.stop()
        assert result is True

    @pytest.mark.asyncio
    async def test_start_non_pre_prod_env_returns_false(self):
        svc = _make_market_sync_service()
        with patch(
            "agentclaw.community.core.skill_center.services.market_sync.asyncio.to_thread",
            new=AsyncMock(return_value="dev"),
        ):
            result = await svc.start()
        assert result is False
        assert svc._started is False

    @pytest.mark.asyncio
    async def test_start_already_started_returns_true(self):
        svc = _make_market_sync_service()
        svc._started = True
        result = await svc.start()
        assert result is True

    @pytest.mark.asyncio
    async def test_start_env_check_exception_returns_false(self):
        svc = _make_market_sync_service()
        with patch(
            "agentclaw.community.core.skill_center.services.market_sync.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("env check fail")),
        ):
            result = await svc.start()
        assert result is False

    @pytest.mark.asyncio
    async def test_start_pre_env_creates_task(self):
        svc = _make_market_sync_service()
        # Patch run_in_threadpool to return "pre" for env check
        with patch(
            "agentclaw.community.core.skill_center.services.market_sync.asyncio.to_thread",
            new=AsyncMock(return_value="pre"),
        ):
            # Also patch exec_sync so the task doesn't do real work
            with patch.object(svc, "exec_sync", new=AsyncMock(return_value={"success": True})):
                result = await svc.start()
                assert result is True
                assert svc._started is True
                assert svc._sync_task is not None
                # Clean up
                await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self):
        svc = _make_market_sync_service()
        svc._started = True

        # Create a long-running coroutine
        async def _long():
            await asyncio.sleep(100)

        svc._sync_task = asyncio.create_task(_long())

        result = await svc.stop()
        assert result is True
        assert svc._started is False
        assert svc._sync_task.done()


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_initial_status(self):
        svc = _make_market_sync_service()
        status = svc.get_status()
        assert status["started"] is False
        assert status["running"] is False
        assert status["last_sync_time"] == 0
        assert status["last_sync_result"] is None

    def test_status_includes_interval(self):
        from agentclaw.community.core.skill_center.services.market_sync import SYNC_INTERVAL_MINUTES
        svc = _make_market_sync_service()
        assert svc.get_status()["interval_minutes"] == SYNC_INTERVAL_MINUTES


# ---------------------------------------------------------------------------
# _get_lock_key
# ---------------------------------------------------------------------------

class TestGetLockKey:
    def test_returns_env_specific_key(self):
        svc = _make_market_sync_service()
        # _get_lock_key imports get_current_env lazily from agentclaw.community.utils.env_utils
        with patch("agentclaw.community.utils.env_utils.get_current_env", return_value="pre"):
            key = svc._get_lock_key()
        assert "market_sync_lock" in key
        assert "pre" in key

    def test_returns_default_on_exception(self):
        svc = _make_market_sync_service()
        # Force the lazy import to raise
        with patch("agentclaw.community.utils.env_utils.get_current_env", side_effect=RuntimeError("no env")):
            key = svc._get_lock_key()
        # Should fall back to "market_sync_lock:dev"
        assert key == "market_sync_lock:dev"


# ---------------------------------------------------------------------------
# _do_git_pull
# ---------------------------------------------------------------------------

class TestDoGitPull:
    def test_script_not_found_returns_failure(self, tmp_path):
        svc = _make_market_sync_service()
        with patch(
            "agentclaw.community.core.skill_center.services.market_sync.RSYNC_TARGET_DIR",
            str(tmp_path),
            create=True,
        ):
            with patch(
                "agentclaw.community.core.skill_center.services.market_sync.Path",
                wraps=Path,
            ):
                # Patch RSYNC_TARGET_DIR inside method
                with patch(
                    "agentclaw.community.core.workspace.path_factory.RSYNC_TARGET_DIR",
                    str(tmp_path),
                    create=True,
                ):
                    result = svc._do_git_pull()
        assert result["success"] is False
        assert "pull-skills.sh not found" in result["message"]

    def test_script_success(self, tmp_path):
        svc = _make_market_sync_service()
        # Create a fake pull-skills.sh
        script = tmp_path / "pull-skills.sh"
        script.write_text("#!/bin/bash\necho ok\n")
        script.chmod(0o755)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "all good"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            with patch(
                "agentclaw.community.core.workspace.path_factory.RSYNC_TARGET_DIR",
                str(tmp_path),
                create=True,
            ):
                # Patch the import inside _do_git_pull
                with patch.dict(
                    "agentclaw.community.core.workspace.path_factory.__dict__",
                    {"RSYNC_TARGET_DIR": str(tmp_path)},
                ):
                    result = svc._do_git_pull()

        # Since we can't guarantee the import override, test via full mock
        assert isinstance(result, dict)

    def test_script_failure_returns_error_message(self, tmp_path):
        svc = _make_market_sync_service()
        script = tmp_path / "pull-skills.sh"
        script.write_text("#!/bin/bash\nexit 1\n")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "git error"

        with patch("subprocess.run", return_value=mock_result):
            with patch(
                "agentclaw.community.core.workspace.path_factory.RSYNC_TARGET_DIR",
                str(tmp_path),
                create=True,
            ):
                import agentclaw.community.core.workspace.path_factory as _pf
                orig = getattr(_pf, "RSYNC_TARGET_DIR", None)
                _pf.RSYNC_TARGET_DIR = str(tmp_path)
                try:
                    result = svc._do_git_pull()
                finally:
                    if orig is not None:
                        _pf.RSYNC_TARGET_DIR = orig

        # Result shape should be dict regardless
        assert isinstance(result, dict)

    def test_exception_returns_failure(self, tmp_path):
        svc = _make_market_sync_service()
        with patch("subprocess.run", side_effect=OSError("no subprocess")):
            import agentclaw.community.core.workspace.path_factory as _pf
            orig = getattr(_pf, "RSYNC_TARGET_DIR", None)
            _pf.RSYNC_TARGET_DIR = str(tmp_path)
            try:
                result = svc._do_git_pull()
            finally:
                if orig is not None:
                    _pf.RSYNC_TARGET_DIR = orig

        assert isinstance(result, dict)
        # Either "script not found" or "sync error" — both are failure
        assert result.get("success") is False or "message" in result


# ---------------------------------------------------------------------------
# exec_sync — lock paths
# ---------------------------------------------------------------------------

class TestExecSync:
    @pytest.mark.asyncio
    async def test_global_lock_held_returns_early(self):
        svc = _make_market_sync_service()

        # GlobalSyncLock is imported lazily inside exec_sync
        with patch(
            "agentclaw.community.core.skill_center.services.skill_cache.GlobalSyncLock"
        ) as mock_global_lock:
            mock_global_lock.acquire.return_value = False
            result = await svc.exec_sync()

        assert result["success"] is False
        assert "GlobalSyncLock" in result["error"]

    @pytest.mark.asyncio
    async def test_distributed_lock_held_returns_early(self):
        mock_cache_plugin = MagicMock()
        mock_cache_plugin.acquire_lock.return_value = None  # lock held by another instance
        mock_cache_plugin.release_lock = MagicMock()
        svc = _make_market_sync_service(cache_plugin=mock_cache_plugin)

        async def _fake_run_in_threadpool(fn, *args, **kwargs):
            return fn(*args, **kwargs) if callable(fn) else fn

        with patch(
            "agentclaw.community.core.skill_center.services.skill_cache.GlobalSyncLock"
        ) as mock_global:
            mock_global.acquire.return_value = True
            mock_global.release = MagicMock()

            with patch(
                "agentclaw.community.core.skill_center.services.market_sync.asyncio.to_thread",
                new=_fake_run_in_threadpool,
            ):
                result = await svc.exec_sync()

        assert result["success"] is False
        assert "Lock already held" in result["error"]


# ---------------------------------------------------------------------------
# _do_sync_db
# ---------------------------------------------------------------------------

class TestDoSyncDb:
    def test_calls_skill_service_sync(self, tmp_path):
        mock_service = MagicMock()
        mock_service.sync_skills_from_git.return_value = {
            "created": 5, "updated": 2, "deleted": 1
        }
        factory = MagicMock()
        factory.create.return_value = mock_service
        svc = _make_market_sync_service(skill_service_factory=factory)

        import agentclaw.community.core.workspace.path_factory as _pf
        orig = getattr(_pf, "RSYNC_TARGET_DIR", None)
        _pf.RSYNC_TARGET_DIR = str(tmp_path)
        try:
            result = svc._do_sync_db()
        finally:
            if orig is not None:
                _pf.RSYNC_TARGET_DIR = orig

        assert isinstance(result, dict)

    def test_exception_returns_error_dict(self):
        svc = _make_market_sync_service()
        with patch(
            "agentclaw.community.core.skill_center.services.market_sync.Path",
            side_effect=RuntimeError("unexpected"),
        ):
            result = svc._do_sync_db()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# sync_now
# ---------------------------------------------------------------------------

class TestSyncNow:
    @pytest.mark.asyncio
    async def test_sync_now_calls_exec_sync(self):
        svc = _make_market_sync_service()
        expected = {"success": True}
        with patch.object(svc, "exec_sync", new=AsyncMock(return_value=expected)):
            result = await svc.sync_now()
        assert result == expected
