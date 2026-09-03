"""Tests for admin_router.

Covers:
- force_success_endpoint happy path (POST /api/v1/admin/force-success)
- Input validation (negative publish_id, missing tenant, empty modifier)
- PublishNotFoundError propagation to 404
- _to_response helper converting ForceSuccessResult → ForceSuccessResponse
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.admin.publish_admin_router import (
    ForceSuccessRequest,
    ForceSuccessResponse,
    UpdateBotStatusRequest,
    UpdateBotStatusResponse,
    _to_response,
    _to_update_bot_status_response,
    router,
)
from secbaas.community.api.publish_manage import (
    PublishNotFoundError,
    UpdateBotStatusResult,
)
from secbaas.community.core.service.publish_manage import ForceSuccessResult
from tests.unit.adapters.web.conftest import iter_api_routes

# ============== Helpers ==============


def _get_admin_dep_callable(router):
    """Extract the Provide[...] callable used as the dependency for
    force_success_endpoint, so we can use it as a dependency_overrides key.

    The admin_router uses `Depends(Provide[ApplicationContainer.services.publish_admin_service])`.
    Creating a new Provide[...] creates a different object, so dependency_overrides
    won't match. We must use the exact same object instance that was captured
    when the route was defined at module import time.
    """
    for r in iter_api_routes(router):
        for dep in r.dependant.dependencies:
            # The force-success route's dependency name is "admin_service"
            return dep.call
    raise RuntimeError("force-success route not found in router")


def _get_endpoint_dep_callable(router, *, path_contains: str):
    """Extract the Provide[...] callable used as the dependency for the
    admin route whose path contains ``path_contains``.

    Each endpoint defines its own ``Depends(Provide[...])`` at module import
    time — a different _Marker instance per route. We must use the exact
    same object instance that was captured at route definition time.
    """
    for r in iter_api_routes(router):
        if path_contains in r.path:
            for dep in r.dependant.dependencies:
                # The admin route's dependency name is "admin_service"
                return dep.call
    raise RuntimeError(
        f"admin route containing path {path_contains!r} not found in router"
    )


# ============== TestClient Fixture ==============


@pytest.fixture
def client() -> TestClient:
    """Fixture providing a TestClient with the admin router mounted.

    Installs a default ``AsyncMock`` override for the
    ``Provide[ApplicationContainer.services.publish_admin_service]``
    dependency so that the monkey-patched ``_Marker.__call__`` does not
    trigger eager DI resolution through an unrelated container.
    """
    app = FastAPI()
    app.include_router(router)
    # Install a default mock so monkey-patched Provide[...] doesn't trigger
    # DI container resolution (which may fail Selector config).
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: AsyncMock()
    with TestClient(app) as c:
        yield c


# ============== ForceSuccessRequest Model ==============


class TestForceSuccessRequest:
    """ForceSuccessRequest input validation."""

    def test_valid_request_construction(self):
        """ForceSuccessRequest can be created with valid inputs."""
        req = ForceSuccessRequest(publish_id=1, modifier="test-user")
        assert req.publish_id == 1
        assert req.modifier == "test-user"

    def test_publish_id_zero_raises_validation_error(self, client):
        """publish_id=0 should return 422 (gt=0 constraint)."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"publish_id": 0, "modifier": "user"},
        )
        assert resp.status_code == 422

    def test_publish_id_negative_raises_validation_error(self, client):
        """Negative publish_id should return 422."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"publish_id": -1, "modifier": "user"},
        )
        assert resp.status_code == 422

    def test_missing_publish_id_returns_422(self, client):
        """Missing publish_id body field should return 422."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"modifier": "user"},
        )
        assert resp.status_code == 422

    def test_empty_modifier_returns_422(self, client):
        """Empty modifier string should return 422 (min_length=1)."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"publish_id": 1, "modifier": ""},
        )
        assert resp.status_code == 422

    def test_missing_modifier_returns_422(self, client):
        """Missing modifier body field should return 422."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"publish_id": 1},
        )
        assert resp.status_code == 422

    def test_modifier_too_long_returns_422(self, client):
        """Modifier > 128 chars should return 422."""
        resp = client.post(
            "/api/v1/admin/force-success?tenant=test",
            json={"publish_id": 1, "modifier": "x" * 129},
        )
        assert resp.status_code == 422

    def test_modifier_boundary_max_len(self, client):
        """Modifier exactly 128 chars should be valid."""
        modifier = "x" * 128
        mock_result = ForceSuccessResult(
            publish_id=1,
            previous_publish_status="PENDING",
            batches_updated=2,
            records_updated=5,
            devices_updated=3,
            bot_updated=True,
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.return_value = mock_result

        dep_callable = _get_admin_dep_callable(client.app.router)
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/force-success?tenant=test",
                json={"publish_id": 1, "modifier": modifier},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200


# ============== ForceSuccessResponse Model ==============


class TestForceSuccessResponse:
    """ForceSuccessResponse model validation."""

    def test_response_construction(self):
        """ForceSuccessResponse can be created with all fields."""
        resp = ForceSuccessResponse(
            publish_id=1,
            previous_publish_status="PENDING",
            batches_updated=2,
            records_updated=5,
            devices_updated=3,
            bot_updated=True,
        )
        assert resp.publish_id == 1
        assert resp.previous_publish_status == "PENDING"
        assert resp.batches_updated == 2
        assert resp.records_updated == 5
        assert resp.devices_updated == 3
        assert resp.bot_updated is True

    def test_response_bot_updated_false(self):
        """ForceSuccessResponse with bot_updated=False."""
        resp = ForceSuccessResponse(
            publish_id=2,
            previous_publish_status="ACTIVE",
            batches_updated=0,
            records_updated=0,
            devices_updated=0,
            bot_updated=False,
        )
        assert resp.bot_updated is False

    def test_response_negative_counts_rejected(self):
        """ForceSuccessResponse rejects negative counts (ge=0 constraint)."""
        with pytest.raises(Exception):
            ForceSuccessResponse(
                publish_id=1,
                previous_publish_status="PENDING",
                batches_updated=-1,
                records_updated=0,
                devices_updated=0,
                bot_updated=False,
            )


# ============== _to_response Helper ==============


class TestToResponse:
    """_to_response converts ForceSuccessResult to ForceSuccessResponse."""

    def test_converts_all_fields(self):
        """_to_response preserves all field values."""
        result = ForceSuccessResult(
            publish_id=42,
            previous_publish_status="PENDING",
            batches_updated=3,
            records_updated=10,
            devices_updated=5,
            bot_updated=True,
        )
        resp = _to_response(result)

        assert isinstance(resp, ForceSuccessResponse)
        assert resp.publish_id == 42
        assert resp.previous_publish_status == "PENDING"
        assert resp.batches_updated == 3
        assert resp.records_updated == 10
        assert resp.devices_updated == 5
        assert resp.bot_updated is True

    def test_zero_updates(self):
        """_to_response handles zero updates correctly."""
        result = ForceSuccessResult(
            publish_id=1,
            previous_publish_status="SUCCESS",
            batches_updated=0,
            records_updated=0,
            devices_updated=0,
            bot_updated=False,
        )
        resp = _to_response(result)

        assert resp.batches_updated == 0
        assert resp.records_updated == 0
        assert resp.devices_updated == 0
        assert resp.bot_updated is False


# ============== force_success_endpoint ==============


class TestForceSuccessEndpoint:
    """Tests for POST /api/v1/admin/force-success endpoint."""

    def test_happy_path_returns_200_with_response_data(self, client):
        """Successful force-success returns 200 with correct ApiResponse body."""
        mock_result = ForceSuccessResult(
            publish_id=1,
            previous_publish_status="PENDING",
            batches_updated=2,
            records_updated=5,
            devices_updated=3,
            bot_updated=True,
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.return_value = mock_result

        dep_callable = _get_admin_dep_callable(client.app.router)
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/force-success?tenant=test-tenant",
                json={"publish_id": 1, "modifier": "admin"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["message"] == "success"
        assert body["data"]["publish_id"] == 1
        assert body["data"]["previous_publish_status"] == "PENDING"
        assert body["data"]["batches_updated"] == 2
        assert body["data"]["records_updated"] == 5
        assert body["data"]["devices_updated"] == 3
        assert body["data"]["bot_updated"] is True

    def test_calls_force_success_with_correct_args(self, client):
        """Endpoint passes publish_id, tenant, and modifier to force_success."""
        mock_result = ForceSuccessResult(
            publish_id=42,
            previous_publish_status="ACTIVE",
            batches_updated=1,
            records_updated=2,
            devices_updated=1,
            bot_updated=False,
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.return_value = mock_result

        dep_callable = _get_admin_dep_callable(client.app.router)
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            client.post(
                "/api/v1/admin/force-success?tenant=my-org",
                json={"publish_id": 42, "modifier": "operator-1"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        mock_admin_svc.force_success.assert_awaited_once_with(
            publish_id=42,
            tenant="my-org",
            modifier="operator-1",
        )

    @pytest.mark.asyncio
    async def test_publish_not_found_propagates_error(self, client):
        """PublishNotFoundError from force_success should propagate to the caller."""
        from secbaas.community.adapters.web.routers.admin.publish_admin_router import (
            force_success_endpoint,
        )

        request = ForceSuccessRequest(publish_id=999, modifier="admin")

        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.side_effect = PublishNotFoundError(publish_id=999)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await force_success_endpoint(
                tenant="test",
                request=request,
                admin_service=mock_admin_svc,
            )

        assert exc_info.value.publish_id == 999

    def test_missing_tenant_query_param_returns_422(self, client):
        """Missing required tenant query param should return 422."""
        resp = client.post(
            "/api/v1/admin/force-success",
            json={"publish_id": 1, "modifier": "admin"},
        )
        assert resp.status_code == 422

    def test_content_type_is_json(self, client):
        """Response Content-Type should be application/json."""
        mock_result = ForceSuccessResult(
            publish_id=1,
            previous_publish_status="PENDING",
            batches_updated=0,
            records_updated=0,
            devices_updated=0,
            bot_updated=False,
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.return_value = mock_result

        dep_callable = _get_admin_dep_callable(client.app.router)
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/force-success?tenant=test",
                json={"publish_id": 1, "modifier": "admin"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert "application/json" in resp.headers["content-type"]

    def test_bot_updated_false_in_response_data(self, client):
        """bot_updated=False is correctly reflected in the response."""
        mock_result = ForceSuccessResult(
            publish_id=5,
            previous_publish_status="FAILED",
            batches_updated=0,
            records_updated=0,
            devices_updated=0,
            bot_updated=False,
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.force_success.return_value = mock_result

        dep_callable = _get_admin_dep_callable(client.app.router)
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/force-success?tenant=test",
                json={"publish_id": 5, "modifier": "admin"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["bot_updated"] is False
        assert body["data"]["batches_updated"] == 0
        assert body["data"]["records_updated"] == 0
        assert body["data"]["devices_updated"] == 0


# ============== UpdateBotStatusRequest Model ==============


class TestUpdateBotStatusRequest:
    """UpdateBotStatusRequest input validation."""

    def test_valid_request_construction(self):
        req = UpdateBotStatusRequest(status="STOPPED", operator="ops.alice")
        assert req.status == "STOPPED"
        assert req.operator == "ops.alice"

    def test_empty_status_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"status": "", "operator": "ops.alice"},
        )
        assert resp.status_code == 422

    def test_status_too_long_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"status": "x" * 33, "operator": "ops.alice"},
        )
        assert resp.status_code == 422

    def test_status_boundary_max_len_ok(self, client):
        status = "x" * 32
        mock_result = UpdateBotStatusResult(
            bot_uuid="BOT_U", previous_status="ACTIVE", new_status=status
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=acme",
                json={"status": status, "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200

    def test_empty_operator_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"status": "STOPPED", "operator": ""},
        )
        assert resp.status_code == 422

    def test_operator_too_long_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"status": "STOPPED", "operator": "x" * 129},
        )
        assert resp.status_code == 422

    def test_operator_boundary_max_len_ok(self, client):
        operator = "x" * 128
        mock_result = UpdateBotStatusResult(
            bot_uuid="BOT_U", previous_status="ACTIVE", new_status="STOPPED"
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=acme",
                json={"status": "STOPPED", "operator": operator},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200

    def test_missing_status_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"operator": "ops.alice"},
        )
        assert resp.status_code == 422

    def test_missing_operator_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status?tenant=acme",
            json={"status": "STOPPED"},
        )
        assert resp.status_code == 422


# ============== UpdateBotStatusResponse Model ==============


class TestUpdateBotStatusResponse:
    """UpdateBotStatusResponse model validation."""

    def test_response_construction(self):
        resp = UpdateBotStatusResponse(
            bot_uuid="BOT_U",
            previous_status="ACTIVE",
            new_status="STOPPED",
        )
        assert resp.bot_uuid == "BOT_U"
        assert resp.previous_status == "ACTIVE"
        assert resp.new_status == "STOPPED"


# ============== _to_update_bot_status_response Helper ==============


class TestToUpdateBotStatusResponse:
    """_to_update_bot_status_response converts UpdateBotStatusResult."""

    def test_converts_all_fields(self):
        result = UpdateBotStatusResult(
            bot_uuid="BOT_U",
            previous_status="ACTIVE",
            new_status="STOPPED",
        )
        resp = _to_update_bot_status_response(result)

        assert isinstance(resp, UpdateBotStatusResponse)
        assert resp.bot_uuid == "BOT_U"
        assert resp.previous_status == "ACTIVE"
        assert resp.new_status == "STOPPED"

    def test_converts_other_values(self):
        result = UpdateBotStatusResult(
            bot_uuid="abc-123",
            previous_status="PENDING",
            new_status="ACTIVE",
        )
        resp = _to_update_bot_status_response(result)

        assert resp.bot_uuid == "abc-123"
        assert resp.previous_status == "PENDING"
        assert resp.new_status == "ACTIVE"


# ============== update_bot_status_endpoint ==============


class TestUpdateBotStatusEndpoint:
    """Tests for POST /api/v1/admin/bots/{bot_uuid}/status endpoint."""

    def test_happy_path_returns_200_with_response_data(self, client):
        mock_result = UpdateBotStatusResult(
            bot_uuid="BOT_U", previous_status="ACTIVE", new_status="STOPPED"
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=acme",
                json={"status": "STOPPED", "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["message"] == "success"
        assert body["data"]["bot_uuid"] == "BOT_U"
        assert body["data"]["previous_status"] == "ACTIVE"
        assert body["data"]["new_status"] == "STOPPED"

    def test_calls_update_bot_status_with_correct_args(self, client):
        mock_result = UpdateBotStatusResult(
            bot_uuid="BOT_U", previous_status="ACTIVE", new_status="STOPPED"
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=other",
                json={"status": "STOPPED", "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        mock_admin_svc.update_bot_status.assert_awaited_once_with(
            bot_uuid="BOT_U",
            status="STOPPED",
            operator="ops.alice",
        )
        # tenant query parameter is advisory only — never forwarded to service.
        kwargs = mock_admin_svc.update_bot_status.await_args.kwargs
        assert "tenant" not in kwargs
        assert "env" not in kwargs

    def test_publish_not_found_propagates_as_404(self, client):
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.side_effect = PublishNotFoundError(
            "Bot BOT_U not found or not active"
        )

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=acme",
                json={"status": "STOPPED", "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 404
        body = resp.json()
        assert "Bot BOT_U not found or not active" in body["detail"]

    def test_missing_tenant_query_param_returns_422(self, client):
        resp = client.post(
            "/api/v1/admin/bots/BOT_U/status",
            json={"status": "STOPPED", "operator": "ops.alice"},
        )
        assert resp.status_code == 422

    def test_response_data_has_exactly_three_keys(self, client):
        mock_result = UpdateBotStatusResult(
            bot_uuid="BOT_U", previous_status="ACTIVE", new_status="STOPPED"
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                "/api/v1/admin/bots/BOT_U/status?tenant=acme",
                json={"status": "STOPPED", "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert set(data.keys()) == {"bot_uuid", "previous_status", "new_status"}

    @pytest.mark.parametrize("bot_uuid", ["BOT_U", "abc-123-def"])
    def test_endpoint_uses_bot_uuid_path_param(self, client, bot_uuid):
        mock_result = UpdateBotStatusResult(
            bot_uuid=bot_uuid, previous_status="ACTIVE", new_status="STOPPED"
        )
        mock_admin_svc = AsyncMock()
        mock_admin_svc.update_bot_status.return_value = mock_result

        dep_callable = _get_endpoint_dep_callable(
            client.app.router, path_contains="/bots/{bot_uuid}/status"
        )
        client.app.dependency_overrides[dep_callable] = lambda: mock_admin_svc
        try:
            resp = client.post(
                f"/api/v1/admin/bots/{bot_uuid}/status?tenant=acme",
                json={"status": "STOPPED", "operator": "ops.alice"},
            )
        finally:
            del client.app.dependency_overrides[dep_callable]

        assert resp.status_code == 200
        assert resp.json()["data"]["bot_uuid"] == bot_uuid
        mock_admin_svc.update_bot_status.assert_awaited_once_with(
            bot_uuid=bot_uuid,
            status="STOPPED",
            operator="ops.alice",
        )
