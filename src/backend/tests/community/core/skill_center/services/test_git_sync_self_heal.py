import pytest
from unittest.mock import MagicMock, AsyncMock
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService


def _make_service(bootstrap_result):
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.sync_bootstrap = AsyncMock(return_value=bootstrap_result)

    async def _run_sync(func, *a, **k):
        return func(*a, **k)

    svc._run_sync = _run_sync
    svc._sync_git_fetch = MagicMock(return_value={"success": True, "fetch_head": "abc"})
    return svc


@pytest.mark.asyncio
async def test_git_fetch_repo_exists_no_bootstrap():
    svc = _make_service(bootstrap_result={"success": True})
    svc.config.local_bare_repo.exists.return_value = True
    result = await svc._git_fetch()
    assert result["success"] is True
    svc.sync_bootstrap.assert_not_called()


@pytest.mark.asyncio
async def test_git_fetch_repo_missing_triggers_bootstrap_then_succeeds():
    svc = _make_service(bootstrap_result={"success": True})
    # 自愈判断时 False，bootstrap 后 True
    svc.config.local_bare_repo.exists.side_effect = [False, True]
    result = await svc._git_fetch()
    svc.sync_bootstrap.assert_awaited_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_git_fetch_repo_missing_bootstrap_fails_returns_failed():
    svc = _make_service(bootstrap_result={"success": False})
    svc.config.local_bare_repo.exists.side_effect = [False, False]
    result = await svc._git_fetch()
    svc.sync_bootstrap.assert_awaited_once()
    assert result["success"] is False
    assert "self-heal" in result["error"]
