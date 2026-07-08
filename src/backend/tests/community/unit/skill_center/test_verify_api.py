"""Unit tests for verify API endpoint."""
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module


def _bind_repos(skill_repo, bot_repo):
    from agentclaw.community.core.bot_management.repository.protocol import BotRepository
    from agentclaw.community.core.skill_center.services.repositories import SkillRepository
    from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher

    class _M(Module):
        def configure(self, binder):
            binder.bind(SkillRepository, to=skill_repo)
            binder.bind(BotRepository, to=bot_repo)
            # The endpoint injects the device-fs dispatcher (the bespoke
            # ArcaVerifyClient was retired for the device-fs seam in B6 Group F);
            # SkillSymlinkVerifyService is mocked below, so a stub suffices.
            binder.bind(DeviceFilesystemDispatcher, to=MagicMock())
    return _M()


def test_verify_symlinks_returns_report():
    from agentclaw.community.adapters.http.skill_center.verify import router

    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_bind_repos(MagicMock(), MagicMock())]))

    mock_failure = MagicMock()
    mock_failure.skill_id = "1"
    mock_failure.skill_name = "skill-A"
    mock_failure.git_path = "center://uuid-A"
    mock_failure.link_name = "skill-A"
    mock_failure.reason = "symlink_not_found"
    mock_failure.expected = "skill-A"
    mock_failure.actual = None

    mock_report = MagicMock()
    mock_report.bot_id = "bot-123"
    mock_report.env = "prod"
    mock_report.total = 2
    mock_report.passed = 1
    mock_report.failed = 1
    mock_report.failures = [mock_failure]

    with patch(
        "agentclaw.community.adapters.http.skill_center.verify.SkillSymlinkVerifyService"
    ) as MockSvc:
        mock_svc_instance = MockSvc.return_value
        mock_svc_instance.verify_bot = AsyncMock(return_value=mock_report)

        client = TestClient(app)
        resp = client.get("/api/v1/skill-center/verify-symlinks?bot_id=bot-123")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["passed"] == 1
        assert data["failed"] == 1
