"""Unit tests for agentclaw.community.adapters.http.oss_to_nas.router.

The router resolves OssToNasRecordRepository and OssToNasSwitchServiceProtocol
via fastapi-injector. Tests bind both to MagicMock instances and exercise the
routes via TestClient.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.core.auth import AuthenticatedIdentity
from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.core.devices.repository.protocol import OssToNasRecordRepository
from agentclaw.community.api.oss_to_nas_switch_service import OssToNasSwitchServiceProtocol

# Fake operator user for bypassing require_operator in tests
_FAKE_OPERATOR = AuthenticatedIdentity(
    id="test_op",
    operatorName="test_op",
    outUserNo="test_op",
)


def _make_record(
    staff_no: str = "staff001",
    bot_id: str = "bot001",
    storage_status: str = "oss",
    batch_no: str = "batch01",
    sub_batch_no: str = "sub01",
    env: str = "pre",
) -> dict:
    return {
        "id": 1,
        "staff_no": staff_no,
        "bot_id": bot_id,
        "bot_info": None,
        "env": env,
        "batch_no": batch_no,
        "sub_batch_no": sub_batch_no,
        "storage_status": storage_status,
        "gmt_create": "2024-01-01T00:00:00",
        "gmt_modified": "2024-01-01T00:00:00",
    }


def _make_client(record_repo, switch_service=None):
    """Build a TestClient with the given mocks bound through the injector.

    Post-R8 the router only resolves two DI deps (record_repo +
    Service API Protocol). Earlier extra bindings (bot_repo,
    bot_service, OssToNasConfig) used to be inline on each endpoint;
    they're now held by the switch service singleton.
    """
    from agentclaw.community.adapters.http.oss_to_nas.router import router

    svc = switch_service or MagicMock()

    class _M(Module):
        def configure(self, binder):
            binder.bind(OssToNasRecordRepository, to=record_repo)
            binder.bind(OssToNasSwitchServiceProtocol, to=svc)

    app = FastAPI()
    app.include_router(router)
    # Override require_operator so tests don't need real auth
    app.dependency_overrides[require_operator] = lambda: _FAKE_OPERATOR
    attach_injector(app, Injector([_M()]))
    return TestClient(app)


# ===========================================================================
# /batch-switch
# ===========================================================================

class TestBatchSwitchEndpoint:
    def test_no_records_returns_empty_result(self):
        record_repo = MagicMock()
        record_repo.query_records_by_batch.return_value = []
        client = _make_client(record_repo)

        resp = client.post("/api/ops/oss-to-nas/batch-switch", json={
            "env": "pre", "batch_no": "b1", "sub_batch_no": "s1", "concurrency": 2
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0

    def test_with_records_calls_batch_switch(self):
        record_repo = MagicMock()
        record_repo.query_records_by_batch.return_value = [_make_record()]
        switch_result = {"total": 1, "succeeded": 1, "failed": 0, "failed_details": []}
        svc = MagicMock()
        svc.batch_switch_with_concurrency = AsyncMock(return_value=switch_result)
        client = _make_client(record_repo, switch_service=svc)

        resp = client.post("/api/ops/oss-to-nas/batch-switch", json={
            "env": "pre", "batch_no": "b1", "sub_batch_no": "s1", "concurrency": 2
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["succeeded"] == 1

    def test_force_mode_skips_status_filter(self):
        record_repo = MagicMock()
        record_repo.query_records_by_batch.return_value = []
        client = _make_client(record_repo)

        client.post("/api/ops/oss-to-nas/batch-switch", json={
            "env": "pre", "batch_no": "b1", "sub_batch_no": "s1",
            "concurrency": 1, "force": True
        })

        record_repo.query_records_by_batch.assert_called_once_with("pre", "b1", "s1", None)

    def test_normal_mode_filters_oss_status(self):
        record_repo = MagicMock()
        record_repo.query_records_by_batch.return_value = []
        client = _make_client(record_repo)

        client.post("/api/ops/oss-to-nas/batch-switch", json={
            "env": "pre", "batch_no": "b1", "sub_batch_no": "s1", "concurrency": 1
        })

        record_repo.query_records_by_batch.assert_called_once_with("pre", "b1", "s1", "oss")


# ===========================================================================
# /single-switch
# ===========================================================================

class TestSingleSwitchEndpoint:
    def test_record_not_found_returns_404(self):
        record_repo = MagicMock()
        record_repo.get_record.return_value = None
        client = _make_client(record_repo)

        resp = client.post("/api/ops/oss-to-nas/single-switch", json={
            "env": "pre", "staff_no": "nobody", "bot_id": "nobot"
        })

        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 404

    def test_success_returns_success_result(self):
        record_repo = MagicMock()
        record_repo.get_record.return_value = _make_record()
        switch_result = {"staff_no": "staff001", "bot_id": "bot001", "status": "success"}
        svc = MagicMock()
        svc.switch_one = AsyncMock(return_value=switch_result)
        client = _make_client(record_repo, switch_service=svc)

        resp = client.post("/api/ops/oss-to-nas/single-switch", json={
            "env": "pre", "staff_no": "staff001", "bot_id": "bot001"
        })

        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "success"

    def test_switch_failure_returns_failed_result(self):
        record_repo = MagicMock()
        record_repo.get_record.return_value = _make_record()
        switch_result = {"staff_no": "staff001", "bot_id": "bot001", "status": "failed", "error": "err"}
        svc = MagicMock()
        svc.switch_one = AsyncMock(return_value=switch_result)
        client = _make_client(record_repo, switch_service=svc)

        resp = client.post("/api/ops/oss-to-nas/single-switch", json={
            "env": "pre", "staff_no": "staff001", "bot_id": "bot001"
        })

        body = resp.json()
        assert body["success"] is False


# ===========================================================================
# /batch-rollback
# ===========================================================================

class TestBatchRollbackEndpoint:
    def test_rollback_returns_affected_count(self):
        record_repo = MagicMock()
        record_repo.batch_update_status.return_value = 5
        client = _make_client(record_repo)

        resp = client.post("/api/ops/oss-to-nas/batch-rollback", json={
            "env": "pre", "batch_no": "b1", "sub_batch_no": "s1"
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["affected"] == 5
        record_repo.batch_update_status.assert_called_once_with("pre", "b1", "s1", "oss")


# ===========================================================================
# /single-rollback
# ===========================================================================

class TestSingleRollbackEndpoint:
    def test_record_not_found_returns_404(self):
        record_repo = MagicMock()
        record_repo.get_record.return_value = None
        client = _make_client(record_repo)

        resp = client.post("/api/ops/oss-to-nas/single-rollback", json={
            "env": "pre", "staff_no": "nobody", "bot_id": "nobot"
        })

        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 404

    def test_rollback_updates_status_to_oss(self):
        record_repo = MagicMock()
        record_repo.get_record.return_value = _make_record(storage_status="nas")
        rollback_result = {"staff_no": "staff001", "bot_id": "bot001", "status": "success"}
        svc = MagicMock()
        svc.rollback_one = AsyncMock(return_value=rollback_result)
        client = _make_client(record_repo, switch_service=svc)

        resp = client.post("/api/ops/oss-to-nas/single-rollback", json={
            "env": "pre", "staff_no": "staff001", "bot_id": "bot001"
        })

        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "success"
