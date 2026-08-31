"""Unit tests for SkillScanService center daily task."""
from unittest.mock import MagicMock, patch

from agentclaw.community.core.skill_center.skill_center_sync_contract import (
    SkillCenterSyncSummary,
)


def _make_service(*, cache_plugin=None, skill_repository=None, sync_service=None):
    from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService

    svc = SkillScanService(
        config={"enabled": True, "enable_scheduled_scan": False},
        cache_plugin=cache_plugin,
        skill_repository=skill_repository,
        skill_center_sync_service=sync_service,
        scanner=MagicMock(),
    )
    svc._started = True  # 跳过 SDK 初始化
    return svc


class TestStartCenterDailyTask:
    def test_skips_in_dev_env(self):
        """dev 环境下 start_center_daily_task 应直接返回 False。"""
        svc = _make_service()
        with patch(
            "agentclaw.community.utils.env_utils.get_current_env_with_gray",
            return_value="dev",
        ):
            result = svc.start_center_daily_task()
        assert result is False
        assert svc._center_daily_task_thread is None

    def test_starts_thread_in_pre_env(self):
        """pre 环境下应启动 center daily task thread。"""
        svc = _make_service()
        with patch(
            "agentclaw.community.utils.env_utils.get_current_env_with_gray",
            return_value="pre",
        ), patch.object(svc, "_center_daily_task_loop"):
            result = svc.start_center_daily_task()
        assert result is True
        assert svc._center_daily_task_thread is not None

    def test_returns_true_if_already_running(self):
        """任务已在运行时再次调用应返回 True，不重复启动。"""
        svc = _make_service()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        svc._center_daily_task_thread = mock_thread

        with patch(
            "agentclaw.community.utils.env_utils.get_current_env_with_gray",
            return_value="prod",
        ):
            result = svc.start_center_daily_task()
        assert result is True


class TestExecCenterTask:
    def test_exec_center_task_delegates_to_materialized_public_sync_once(self):
        """Legacy scheduler must not enumerate or scan all center rows itself."""
        mock_skill_repo = MagicMock()

        mock_sync_svc = MagicMock()
        mock_sync_svc.sync.return_value = SkillCenterSyncSummary(
            scanned=2,
            updated=1,
            unchanged=1,
            failed=0,
            failures=(),
        )

        mock_cache = MagicMock()
        mock_cache.acquire_lock.return_value = "lock-val"

        svc = _make_service(
            cache_plugin=mock_cache,
            skill_repository=mock_skill_repo,
            sync_service=mock_sync_svc,
        )

        with patch(
            "agentclaw.community.utils.env_utils.get_current_env",
            return_value="pre",
        ):
            result = svc.exec_center_task()

        assert result["success"] is True
        assert result["total"] == 2
        assert result["success_count"] == 2
        mock_sync_svc.sync.assert_called_once_with()
        mock_skill_repo.list_published_center_skills.assert_not_called()

    def test_exec_center_task_skips_when_lock_held(self):
        """分布式锁被占时应跳过执行。"""
        mock_cache = MagicMock()
        mock_cache.acquire_lock.return_value = None  # 锁被占

        svc = _make_service(cache_plugin=mock_cache)

        with patch(
            "agentclaw.community.utils.env_utils.get_current_env",
            return_value="pre",
        ):
            result = svc.exec_center_task()

        assert result["success"] is False
        assert "lock" in result["error"].lower()
