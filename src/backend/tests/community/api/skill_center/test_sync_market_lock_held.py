"""Route-level regression tests for POST /api/skills/market/sync.

锁占用语义回归：sync_market 路由必须把"锁被占用"（两把锁任一）当成
"同步进行中"返回 200 友好提示，而不是 500。真实失败仍须 500。

原始 bug：service 层 sync_repo_with_lock 未透传底层 error 键，导致路由
result.get("error") 拿不到锁占用标识，把锁占用当 500 抛出
（{"detail": "Distributed lock held"}）。

本文件在路由层验证最终的用户可见契约——service 单测只覆盖到 service 边界，
挡不住路由判定逻辑的回归（例如只匹配一把锁、漏掉 GlobalSyncLock held）。
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.skill_center.skills import router as skills_router


@pytest.fixture
def mock_ctx():
    return RequestContext(user_id="user_001", bot_id="default")


@contextmanager
def _sync_market_di_app(mock_ctx, sync_repo_result):
    """Build a TestClient where SkillService.sync_repo_with_lock returns
    ``sync_repo_result`` (a plain dict — the method is sync, called via
    run_in_threadpool).  Yields (client, mock_skill_service)."""
    mock_skill_service = MagicMock()
    # sync_repo_with_lock 是同步方法（run_in_threadpool 调用）→ 普通 MagicMock
    mock_skill_service.sync_repo_with_lock = MagicMock(return_value=sync_repo_result)
    # sync_skills_from_git 只在 synced=True 时才会被调用；这里锁占用/失败用例不会触发
    mock_skill_service.sync_skills_from_git = MagicMock(
        return_value={"created": 0, "updated": 0, "deleted": 0, "failed": 0}
    )

    mock_skill_service_factory = MagicMock()
    mock_skill_service_factory.create.return_value = mock_skill_service

    mock_path_factory = MagicMock()
    mock_path_factory.get_bot_skills_dir.return_value = MagicMock()
    mock_path_factory.get_bot_skills_local_dir.return_value = MagicMock()
    mock_path_factory.get_bot_engine_dir.return_value = MagicMock()
    mock_path_factory.get_bot_skills_repo_dir.return_value = MagicMock()

    mock_bot_repo = MagicMock()
    mock_bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": mock_ctx.bot_id,
        "owner_id": mock_ctx.user_id,
        "active_engine": "openclaw",
        "bot_type": "personal",
        "status": "ACTIVE",
    }

    app = FastAPI()
    app.include_router(skills_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            from agentclaw.community.core.repository.protocols.bot import BotRepository
            from agentclaw.community.core.skill_center.factories import SkillServiceFactory
            from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

            from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
            binder.bind(SkillServiceFactory, to=mock_skill_service_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_skill_service_factory)
            binder.bind(WorkspacePathFactory, to=mock_path_factory)
            binder.bind(BotRepository, to=mock_bot_repo)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_skill_service


class TestSyncMarketLockHeld:
    """锁占用 → 200 友好提示；真实失败 → 500。"""

    @pytest.mark.parametrize("lock_error", ["Distributed lock held", "GlobalSyncLock held"])
    def test_lock_held_returns_200_friendly(self, mock_ctx, lock_error):
        """两把锁任一被占用，都应返回 200 + "同步进行中"，而非 500。"""
        result = {"success": False, "synced": False, "error": lock_error, "message": lock_error}
        with _sync_market_di_app(mock_ctx, result) as (client, svc):
            resp = client.post("/api/skills/market/sync")

        assert resp.status_code == 200, f"{lock_error} should NOT 500"
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["synced"] is False
        assert body["data"]["message"] == "同步进行中，请稍后再试"
        # 锁占用时不应继续走 DB 同步
        svc.sync_skills_from_git.assert_not_called()

    def test_genuine_failure_returns_500(self, mock_ctx):
        """非锁占用的真实失败必须仍然 500，不能被锁占用分支吞掉。"""
        result = {
            "success": False,
            "synced": False,
            "error": "Git fetch failed: boom",
            "message": "Git fetch failed: boom",
        }
        with _sync_market_di_app(mock_ctx, result) as (client, _svc):
            resp = client.post("/api/skills/market/sync")

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Git fetch failed: boom"

    def test_success_returns_200_and_runs_db_sync(self, mock_ctx):
        """成功且实际同步过 → 200，且触发 DB 同步。"""
        result = {
            "success": True,
            "synced": True,
            "error": None,
            "message": "Sync completed",
            "last_sync": None,
            "next_sync_in": 0,
        }
        with _sync_market_di_app(mock_ctx, result) as (client, svc):
            resp = client.post("/api/skills/market/sync")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["synced"] is True
        svc.sync_skills_from_git.assert_called_once()
