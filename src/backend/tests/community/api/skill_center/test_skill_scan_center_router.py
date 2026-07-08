"""Unit tests for POST /api/skill-scan/scan/center."""
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.skill_center.skill_scan import router as skill_scan_router
from agentclaw.community.core.skill_center.services.skill_center_sync_service import (
    SkillCenterSyncService,
)


def _build_client(mock_svc):
    app = FastAPI()
    app.include_router(skill_scan_router)

    class _M(Module):
        def configure(self, binder):
            from agentclaw.community.api.skill_center_sync_service import SkillCenterSyncServiceProtocol
            binder.bind(SkillCenterSyncService, to=mock_svc)
            binder.bind(SkillCenterSyncServiceProtocol, to=mock_svc)

    attach_injector(app, Injector([_M()]))
    return TestClient(app, raise_server_exceptions=False)


class TestScanCenterRouter:
    def test_scan_center_single_uuid_success(self):
        """传入单个 skill_uuid 时应扫描并返回成功。"""
        mock_svc = MagicMock()
        mock_svc.scan_after_sync.return_value = None
        client = _build_client(mock_svc)

        resp = client.post(
            "/api/skill-scan/scan/center",
            json={"skill_uuids": ["uuid-aaa"], "env": "dev"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 1
        assert data["success_count"] == 1
        mock_svc.scan_after_sync.assert_called_once_with("uuid-aaa", "dev")

    def test_scan_center_multiple_uuids(self):
        """传入多个 uuid 时应逐个扫描。"""
        mock_svc = MagicMock()
        mock_svc.scan_after_sync.return_value = None
        client = _build_client(mock_svc)

        resp = client.post(
            "/api/skill-scan/scan/center",
            json={"skill_uuids": ["uuid-1", "uuid-2", "uuid-3"], "env": "prod"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["success_count"] == 3
        assert mock_svc.scan_after_sync.call_count == 3

    def test_scan_center_partial_failure(self):
        """单个 uuid 扫描抛异常时，其余继续，success_count 正确计算。"""
        def side_effect(uuid, env):
            if uuid == "uuid-bad":
                raise RuntimeError("scan failed")

        mock_svc = MagicMock()
        mock_svc.scan_after_sync.side_effect = side_effect
        client = _build_client(mock_svc)

        resp = client.post(
            "/api/skill-scan/scan/center",
            json={"skill_uuids": ["uuid-ok", "uuid-bad"], "env": "dev"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["success_count"] == 1

    def test_scan_center_empty_uuids_returns_400(self):
        """空列表应返回 400。"""
        client = _build_client(MagicMock())
        resp = client.post(
            "/api/skill-scan/scan/center",
            json={"skill_uuids": [], "env": "dev"},
        )
        assert resp.status_code == 400
