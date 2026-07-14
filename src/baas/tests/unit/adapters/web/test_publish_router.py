"""
Publish Router 单元测试

测试 publish_router.py 中的所有 10 个路由处理器 + callback_router 的 1 个处理器。
使用 TestClient + app.dependency_overrides 进行验证。
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.bot_service.publish_router import (
    callback_router,
    router,
)
from secbaas.community.api.publish_manage import (
    DeviceCallbackRequest,
    DrainResult,
    PublishConfig,
    PublishProgressResponse,
    PublishResponse,
    PublishType,
)
from tests.unit.adapters.web.conftest import iter_api_routes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEASE_ID = "0" * 32  # 32 chars for request_id
VALID_REQUEST_ID = "487ec32cf90b424195f6786651ac1ba5"
NOW = datetime(2026, 5, 23, 12, 0, 0)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_publish_response(
    id_: int = 1,
    bot_id: int = 10,
    publish_type: str = "CREATE",
    status: str = "PENDING",
    stage: str | None = "PREPUB",
    creator: str = "admin",
    modifier: str = "admin",
) -> PublishResponse:
    return PublishResponse(
        id=id_,
        bot_id=bot_id,
        publish_type=publish_type,
        status=status,
        stage=stage,
        extra_config=None,
        creator=creator,
        modifier=modifier,
        gmt_create=NOW,
        gmt_modified=NOW,
        request_id=VALID_REQUEST_ID,
    )


def make_drain_result(
    success: bool = True,
    sessions_remaining: int = 0,
    duration_seconds: float = 2.5,
    timeout_reached: bool = False,
) -> DrainResult:
    return DrainResult(
        success=success,
        sessions_remaining=sessions_remaining,
        duration_seconds=duration_seconds,
        timeout_reached=timeout_reached,
    )


def make_progress_response(
    publish_id: int = 1,
    status: str = "ACTIVE",
    current_stage: str | None = "PROD_FIRST_BATCH",
) -> dict:
    """Build a minimal PublishProgressResponse for use in mocks."""
    from secbaas.community.api.publish_manage import ProgressSummary, ProgressTimeline

    return PublishProgressResponse(
        publish_id=publish_id,
        status=status,
        current_stage=current_stage,
        overall_progress=ProgressSummary(
            total_batches=5,
            completed_batches=2,
            total_devices=100,
            processed_devices=40,
            failed_devices=1,
            progress_percentage=40.0,
        ),
        stages=[],
        timeline=ProgressTimeline(gmt_create=NOW, gmt_modified=NOW),
        device_details=[],
        failed_devices=[],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _install_mock_overrides(app, mock_svc):
    """Replace every Provide[...] dependency with *mock_svc*."""
    from dependency_injector.wiring import Provide as ProvideCls

    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, ProvideCls):
                app.dependency_overrides[dep.call] = lambda: mock_svc


_mock_svc = AsyncMock()


@pytest.fixture
def client():
    """Create TestClient with both routers mounted and default mock overrides.

    Every ``Provide[...]`` dependency is replaced with a default
    ``AsyncMock`` so that the monkey-patched ``_Marker.__call__``
    (applied by ``app.py`` at import time) never triggers eager DI
    resolution through an unrelated container.  Individual tests that
    need a configured mock call ``_install_override`` to replace it.
    """
    app = FastAPI()
    app.include_router(router)
    app.include_router(callback_router)
    _install_mock_overrides(app, _mock_svc)
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


@pytest.fixture
def mock_service():
    """Provide an AsyncMock that can be attached to DefaultPublishService."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# Helper: install/remove dependency override
# ---------------------------------------------------------------------------


def _install_override(client, mock_svc):
    """Replace the default mock with a configured one for all routes."""
    _install_mock_overrides(client.app, mock_svc)


# ---------------------------------------------------------------------------
# POST /api/v1/publishes — create_publish
# ---------------------------------------------------------------------------


class TestCreatePublish:
    """POST /api/v1/publishes"""

    def test_create_publish_success(self, client):
        pub = make_publish_response()
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 10,
            "publish_type": "CREATE",
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.create_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["id"] == 1
        assert body["data"]["bot_id"] == 10
        assert body["data"]["publish_type"] == "CREATE"
        assert body["data"]["status"] == "PENDING"

        mock_svc.create_publish.assert_awaited_once_with(
            tenant="test_tenant",
            bot_id=10,
            publish_type=PublishType.CREATE,
            operator="admin",
            config=None,
            request_id=VALID_REQUEST_ID,
        )

    def test_create_publish_with_config(self, client):
        pub = make_publish_response(
            id_=2, publish_type="UPDATE", status="ACTIVE", stage="GRAY"
        )
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 20,
            "publish_type": "UPDATE",
            "operator": "user2",
            "config": {
                "drain_timeout_seconds": 60,
                "batch_capacity_default": 10,
            },
        }

        mock_svc = AsyncMock()
        mock_svc.create_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == 2
        assert body["data"]["publish_type"] == "UPDATE"

        call_kwargs = mock_svc.create_publish.await_args.kwargs
        assert call_kwargs["tenant"] == "test_tenant"
        assert call_kwargs["bot_id"] == 20
        assert call_kwargs["publish_type"] == PublishType.UPDATE
        assert call_kwargs["operator"] == "user2"
        assert isinstance(call_kwargs["config"], PublishConfig)
        assert call_kwargs["config"].drain_timeout_seconds == 60
        assert call_kwargs["config"].batch_capacity_default == 10

    def test_create_publish_publish_type_scale_up(self, client):
        pub = make_publish_response(id_=3, publish_type="SCALE_UP", stage=None)
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 30,
            "publish_type": "SCALE_UP",
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.create_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["publish_type"] == "SCALE_UP"
        mock_svc.create_publish.assert_awaited_once_with(
            tenant="test_tenant",
            bot_id=30,
            publish_type=PublishType.SCALE_UP,
            operator="admin",
            config=None,
            request_id=VALID_REQUEST_ID,
        )

    def test_create_publish_missing_required_fields(self, client):
        response = client.post("/api/v1/publishes?tenant=test_tenant", json={})
        assert response.status_code == 422

    def test_create_publish_invalid_request_id_too_short(self, client):
        payload = {
            "request_id": "short",
            "bot_id": 10,
            "publish_type": "CREATE",
            "operator": "admin",
        }
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)
        assert response.status_code == 422

    def test_create_publish_invalid_bot_id_zero(self, client):
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 0,
            "publish_type": "CREATE",
            "operator": "admin",
        }
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)
        assert response.status_code == 422

    def test_create_publish_invalid_publish_type(self, client):
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 10,
            "publish_type": "INVALID_TYPE",
            "operator": "admin",
        }
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/publishes/{publish_id} — get_publish
# ---------------------------------------------------------------------------


class TestGetPublish:
    """GET /api/v1/publishes/{publish_id}"""

    def test_get_publish_success(self, client):
        pub = make_publish_response(id_=1, status="ACTIVE")

        mock_svc = AsyncMock()
        mock_svc.get_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.get("/api/v1/publishes/1?tenant=test_tenant")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["id"] == 1
        assert body["data"]["status"] == "ACTIVE"

        mock_svc.get_publish.assert_awaited_once_with(
            tenant="test_tenant", publish_id=1
        )

    def test_get_publish_not_found(self, client):
        mock_svc = AsyncMock()
        mock_svc.get_publish = AsyncMock(return_value=None)

        _install_override(client, mock_svc)
        response = client.get("/api/v1/publishes/999?tenant=test_tenant")

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "PUBLISH_NOT_FOUND"
        assert "999" in body["detail"]["message"]

    def test_get_publish_invalid_id_type(self, client):
        response = client.get("/api/v1/publishes/abc?tenant=test_tenant")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/publishes/{publish_id}/progress — get_publish_progress
# ---------------------------------------------------------------------------


class TestGetPublishProgress:
    """GET /api/v1/publishes/{publish_id}/progress"""

    def test_get_progress_success(self, client):
        progress = make_progress_response()

        mock_svc = AsyncMock()
        mock_svc.get_publish_progress = AsyncMock(return_value=progress)

        _install_override(client, mock_svc)
        response = client.get("/api/v1/publishes/1/progress?tenant=test_tenant")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["publish_id"] == 1
        assert body["data"]["status"] == "ACTIVE"
        assert body["data"]["current_stage"] == "PROD_FIRST_BATCH"
        assert body["data"]["overall_progress"]["progress_percentage"] == 40.0

        mock_svc.get_publish_progress.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            include_devices=False,
        )

    def test_get_progress_with_devices(self, client):
        progress = make_progress_response()

        mock_svc = AsyncMock()
        mock_svc.get_publish_progress = AsyncMock(return_value=progress)

        _install_override(client, mock_svc)
        response = client.get(
            "/api/v1/publishes/1/progress?tenant=test_tenant&include_devices=true"
        )

        assert response.status_code == 200
        mock_svc.get_publish_progress.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            include_devices=True,
        )

    def test_get_progress_not_found(self, client):
        mock_svc = AsyncMock()
        mock_svc.get_publish_progress = AsyncMock(return_value=None)

        _install_override(client, mock_svc)
        response = client.get("/api/v1/publishes/999/progress?tenant=test_tenant")

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "PUBLISH_NOT_FOUND"
        assert "999" in body["detail"]["message"]


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/approve — approve_stage
# ---------------------------------------------------------------------------


class TestApproveStage:
    """POST /api/v1/publishes/{publish_id}/approve"""

    def test_approve_success(self, client):
        pub = make_publish_response(id_=1, status="ACTIVE", stage="GRAY")
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "comment": "Approved for gray release",
        }

        mock_svc = AsyncMock()
        mock_svc.approve_stage = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/approve?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "ACTIVE"

        mock_svc.approve_stage.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            comment="Approved for gray release",
        )

    def test_approve_without_comment(self, client):
        pub = make_publish_response(status="PENDING")
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.approve_stage = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/approve?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        mock_svc.approve_stage.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            comment=None,
        )

    def test_approve_missing_operator(self, client):
        payload = {"request_id": VALID_REQUEST_ID}
        response = client.post(
            "/api/v1/publishes/1/approve?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422

    def test_approve_comment_too_long(self, client):
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "comment": "x" * 513,
        }
        response = client.post(
            "/api/v1/publishes/1/approve?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/reject — reject_publish
# ---------------------------------------------------------------------------


class TestRejectPublish:
    """POST /api/v1/publishes/{publish_id}/reject"""

    def test_reject_success(self, client):
        pub = make_publish_response(id_=1, status="REJECTED", stage=None)
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "reason": "Configuration error in batch size",
        }

        mock_svc = AsyncMock()
        mock_svc.reject_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/reject?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "REJECTED"

        mock_svc.reject_publish.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            reason="Configuration error in batch size",
        )

    def test_reject_missing_reason(self, client):
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }
        response = client.post(
            "/api/v1/publishes/1/reject?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422

    def test_reject_reason_too_long(self, client):
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "reason": "x" * 513,
        }
        response = client.post(
            "/api/v1/publishes/1/reject?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/revoke — revoke_publish
# ---------------------------------------------------------------------------


class TestRevokePublish:
    """POST /api/v1/publishes/{publish_id}/revoke"""

    def test_revoke_success(self, client):
        pub = make_publish_response(id_=1, status="REVOKED", stage=None)
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "reason": "No longer needed",
        }

        mock_svc = AsyncMock()
        mock_svc.revoke_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/revoke?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "REVOKED"

        mock_svc.revoke_publish.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            reason="No longer needed",
        )

    def test_revoke_without_reason(self, client):
        pub = make_publish_response(status="REVOKED")
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.revoke_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/revoke?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        mock_svc.revoke_publish.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            reason=None,
        )


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/execute — execute_stage
# ---------------------------------------------------------------------------


class TestExecuteStage:
    """POST /api/v1/publishes/{publish_id}/execute"""

    def test_execute_success(self, client):
        drain = make_drain_result(
            success=True, sessions_remaining=0, duration_seconds=5.0
        )
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.execute_stage = AsyncMock(return_value=drain)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/execute?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        result = body["data"]
        assert result["success"] is True
        assert result["sessions_remaining"] == 0
        assert result["duration_seconds"] == 5.0
        assert result["timeout_reached"] is False

        mock_svc.execute_stage.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
        )

    def test_execute_timeout_reached(self, client):
        drain = make_drain_result(
            success=False,
            sessions_remaining=3,
            duration_seconds=30.0,
            timeout_reached=True,
        )
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.execute_stage = AsyncMock(return_value=drain)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/execute?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        result = body["data"]
        assert result["success"] is False
        assert result["sessions_remaining"] == 3
        assert result["timeout_reached"] is True

    def test_execute_missing_operator(self, client):
        payload = {"request_id": VALID_REQUEST_ID}
        response = client.post(
            "/api/v1/publishes/1/execute?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/complete — complete_publish
# ---------------------------------------------------------------------------


class TestCompletePublish:
    """POST /api/v1/publishes/{publish_id}/complete"""

    def test_complete_success(self, client):
        pub = make_publish_response(id_=1, status="SUCCESS", stage=None)
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.complete_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/complete?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "SUCCESS"

        mock_svc.complete_publish.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
        )

    def test_complete_missing_operator(self, client):
        payload = {"request_id": VALID_REQUEST_ID}
        response = client.post(
            "/api/v1/publishes/1/complete?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/publishes/{publish_id}/retry — retry_publish
# ---------------------------------------------------------------------------


class TestRetryPublish:
    """POST /api/v1/publishes/{publish_id}/retry"""

    def test_retry_success(self, client):
        pub = make_publish_response(id_=2, bot_id=10, status="PENDING", stage="PREPUB")
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.retry_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/retry?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == 2
        assert body["data"]["status"] == "PENDING"

        mock_svc.retry_publish.assert_awaited_once_with(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            request_id=VALID_REQUEST_ID,
            config=None,
        )

    def test_retry_with_config(self, client):
        pub = make_publish_response(id_=3, status="PENDING")
        payload = {
            "request_id": VALID_REQUEST_ID,
            "operator": "admin",
            "config": {
                "drain_timeout_seconds": 120,
                "batch_capacity_default": 20,
            },
        }

        mock_svc = AsyncMock()
        mock_svc.retry_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publishes/1/retry?tenant=test_tenant", json=payload
        )

        assert response.status_code == 200
        call_kwargs = mock_svc.retry_publish.await_args.kwargs
        assert isinstance(call_kwargs["config"], PublishConfig)
        assert call_kwargs["config"].drain_timeout_seconds == 120
        assert call_kwargs["config"].batch_capacity_default == 20

    def test_retry_missing_operator(self, client):
        payload = {"request_id": VALID_REQUEST_ID}
        response = client.post(
            "/api/v1/publishes/1/retry?tenant=test_tenant", json=payload
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/publish/device-callback — device_callback
# ---------------------------------------------------------------------------


class TestDeviceCallback:
    """POST /api/v1/publish/device-callback"""

    def test_device_callback_success(self, client):
        result = {"status": "processed", "publish_id": 1, "device_uuid": "DEV-001"}

        mock_svc = AsyncMock()
        mock_svc.handle_device_callback = AsyncMock(return_value=result)

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publish/device-callback",
            json={
                "device_uuid": "DEV-001",
                "publish_id": 1,
                "event_type": "start",
                "result_status": "SUCCESS",
                "exit_code": 0,
                "stdout": "Hook executed successfully",
                "stderr": "",
                "tenant": "test_tenant",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "processed"

        mock_svc.handle_device_callback.assert_awaited_once()
        call_arg = mock_svc.handle_device_callback.await_args.args[0]
        assert isinstance(call_arg, DeviceCallbackRequest)
        assert call_arg.device_uuid == "DEV-001"
        assert call_arg.publish_id == 1

    def test_device_callback_not_found(self, client):
        mock_svc = AsyncMock()
        mock_svc.handle_device_callback = AsyncMock(
            side_effect=Exception("Device DEV-999 not found in publish records")
        )

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publish/device-callback",
            json={
                "device_uuid": "DEV-999",
                "publish_id": 1,
                "event_type": "start",
                "result_status": "SUCCESS",
                "tenant": "test_tenant",
            },
        )

        assert response.status_code == 404
        body = response.json()
        assert body["detail"]["error_code"] == "DEVICE_NOT_FOUND"

    def test_device_callback_generic_error(self, client):
        mock_svc = AsyncMock()
        mock_svc.handle_device_callback = AsyncMock(
            side_effect=Exception("Internal processing error")
        )

        _install_override(client, mock_svc)
        response = client.post(
            "/api/v1/publish/device-callback",
            json={
                "device_uuid": "DEV-001",
                "publish_id": 1,
                "event_type": "start",
                "result_status": "SUCCESS",
                "tenant": "test_tenant",
            },
        )

        assert response.status_code == 500

    def test_device_callback_validation_missing_fields(self, client):
        response = client.post("/api/v1/publish/device-callback", json={})
        assert response.status_code == 422

    def test_device_callback_invalid_event_type(self, client):
        response = client.post(
            "/api/v1/publish/device-callback",
            json={
                "device_uuid": "DEV-001",
                "publish_id": 1,
                "event_type": "invalid",
                "result_status": "SUCCESS",
                "tenant": "test_tenant",
            },
        )
        assert response.status_code == 422

    def test_device_callback_invalid_publish_id(self, client):
        response = client.post(
            "/api/v1/publish/device-callback",
            json={
                "device_uuid": "DEV-001",
                "publish_id": 0,
                "event_type": "start",
                "result_status": "SUCCESS",
                "tenant": "test_tenant",
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PublishType enum resolution (StrEnum validates input strings)
# ---------------------------------------------------------------------------


class TestPublishTypeResolution:
    """Test that PublishType enum correctly resolves from strings.

    The router passes request.publish_type directly to service methods,
    so Pydantic validates this. These tests verify that all valid
    publish types are accepted and invalid ones rejected.
    """

    @pytest.mark.parametrize(
        "publish_type",
        ["CREATE", "UPDATE", "RESTART", "SCALE_UP", "SCALE_DOWN", "DESTROY"],
    )
    def test_all_valid_publish_types_accepted(self, client, publish_type):
        pub = make_publish_response(publish_type=publish_type)
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 10,
            "publish_type": publish_type,
            "operator": "admin",
        }

        mock_svc = AsyncMock()
        mock_svc.create_publish = AsyncMock(return_value=pub)

        _install_override(client, mock_svc)
        response = client.post("/api/v1/publishes?tenant=test_tenant", json=payload)

        assert response.status_code == 200
        call_kwargs = mock_svc.create_publish.await_args.kwargs
        assert call_kwargs["publish_type"].value == publish_type


# ---------------------------------------------------------------------------
# Cross-cutting concerns
# ---------------------------------------------------------------------------


class TestValidation:
    """Cross-cutting validation tests."""

    def test_create_requires_tenant_returns_validation_error(self, client):
        """Omitting tenant on POST returns an HTTP error (source uses Ellipsis default)."""
        payload = {
            "request_id": VALID_REQUEST_ID,
            "bot_id": 10,
            "publish_type": "CREATE",
            "operator": "admin",
        }
        response = client.post("/api/v1/publishes", json=payload)
        assert response.status_code >= 400
