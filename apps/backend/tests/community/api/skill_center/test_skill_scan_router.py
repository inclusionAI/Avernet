"""Tests for skill_scan API router.

Uses Injector rebinding to swap the singleton ``SkillScanService`` for a mock.
"""
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.skill_center.skill_scan import router as skill_scan_router
from agentclaw.community.api.skill_scan_service import SkillScanServiceProtocol
from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService


def _bind_scan(svc):
    class _M(Module):
        def configure(self, binder):
            binder.bind(SkillScanService, to=svc)
            binder.bind(SkillScanServiceProtocol, to=svc)
    return _M()


def _make_client(svc):
    app = FastAPI()
    app.include_router(skill_scan_router)
    attach_injector(app, Injector([_bind_scan(svc)]))
    return TestClient(app, raise_server_exceptions=False)


def _make_service(success=True, results=None, success_count=1, total=1, error=None):
    svc = MagicMock()
    if success:
        svc.exec_task.return_value = {
            "success": True,
            "results": results or [{"skill_path": "path/to/skill", "success": True}],
            "success_count": success_count,
            "total": total,
        }
    else:
        svc.exec_task.return_value = {
            "success": False,
            "error": error or "Task failed",
        }
    return svc


# ==================== GET /api/skill-scan/status ====================

class TestGetServiceStatus:
    def test_returns_200_with_expected_fields(self):
        client = _make_client(MagicMock())
        resp = client.get("/api/skill-scan/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["service_started"] is True
        assert data["scheduler_started"] is False
        assert data["daily_task_running"] is False
        assert "message" in data


# ==================== POST /api/skill-scan/daily-task/start ====================

class TestStartDailyTask:
    def test_returns_501(self):
        client = _make_client(MagicMock())
        resp = client.post(
            "/api/skill-scan/daily-task/start",
            json={"git_url": "https://github.com/example/repo"},
        )
        assert resp.status_code == 501
        assert "not supported" in resp.json()["detail"]


# ==================== POST /api/skill-scan/daily-task/stop ====================

class TestStopDailyTask:
    def test_returns_501(self):
        client = _make_client(MagicMock())
        resp = client.post("/api/skill-scan/daily-task/stop")
        assert resp.status_code == 501
        assert "not supported" in resp.json()["detail"]


# ==================== POST /api/skill-scan/scan/git ====================

class TestScanGit:
    def test_success(self):
        svc = _make_service()
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/git", json={"git_url": "https://github.com/example/repo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 1
        assert data["success_count"] == 1

    def test_task_failure_returns_500(self):
        svc = _make_service(success=False, error="git clone failed")
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/git", json={"git_url": "https://github.com/example/repo"})
        assert resp.status_code == 500
        assert "git clone failed" in resp.json()["detail"]

    def test_exception_returns_500(self):
        svc = MagicMock()
        svc.exec_task.side_effect = Exception("unexpected error")
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/git", json={"git_url": "https://github.com/example/repo"})
        assert resp.status_code == 500

    def test_missing_git_url_returns_422(self):
        client = _make_client(MagicMock())
        resp = client.post("/api/skill-scan/scan/git", json={})
        assert resp.status_code == 422

    def test_with_private_token(self):
        svc = _make_service()
        client = _make_client(svc)
        resp = client.post(
            "/api/skill-scan/scan/git",
            json={"git_url": "https://github.com/private/repo", "private_token": "abc123"},
        )
        assert resp.status_code == 200
        call_kwargs = svc.exec_task.call_args
        assert call_kwargs.kwargs.get("private_token") == "abc123"

    def test_multiple_results(self):
        svc = _make_service(
            results=[
                {"skill_path": "path/skill1", "success": True},
                {"skill_path": "path/skill2", "success": True},
            ],
            success_count=2,
            total=2,
        )
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/git", json={"git_url": "https://github.com/example/repo"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["results"]) == 2


# ==================== POST /api/skill-scan/scan/skill ====================

class TestScanSkill:
    def test_success(self):
        svc = MagicMock()
        svc.scan_skill.return_value = {"name": "my_skill", "status": "ok"}
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/skill", json={"skill_path": "/path/to/skill"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 1
        assert data["success_count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["skill_path"] == "/path/to/skill"
        assert data["results"][0]["success"] is True

    def test_exception_returns_500(self):
        svc = MagicMock()
        svc.scan_skill.side_effect = Exception("scan failed")
        client = _make_client(svc)
        resp = client.post("/api/skill-scan/scan/skill", json={"skill_path": "/bad/path"})
        assert resp.status_code == 500
        assert "scan failed" in resp.json()["detail"]

    def test_missing_skill_path_returns_422(self):
        client = _make_client(MagicMock())
        resp = client.post("/api/skill-scan/scan/skill", json={})
        assert resp.status_code == 422
