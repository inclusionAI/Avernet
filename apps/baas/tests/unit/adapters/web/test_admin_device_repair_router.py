"""Unit tests for the admin device-repair endpoint.

Covers ``POST /api/v1/admin/devices/{device_uuid}/repair``
(``update_device_status_endpoint``) and its request/response models:

- request validation: at-least-one-field rule, enum status, provider id bounds
- success envelope mapping, including previous/new provider binding
- 404 when the service reports the device missing
- the documented route path (``/repair``, not the old ``/status``)
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.admin.publish_admin_router import (
    UpdateDeviceStatusRequest,
    UpdateDeviceStatusResponse,
    _to_update_status_response,
    router,
)
from secbaas.community.api.device_manage import DeviceStatus
from secbaas.community.api.publish_manage import (
    PublishNotFoundError,
    UpdateDeviceStatusResult,
)
from tests.unit.adapters.web.conftest import iter_api_routes

REPAIR_PATH = "/api/v1/admin/devices/{device_uuid}/repair"


@pytest.fixture
def client() -> TestClient:
    """TestClient with the admin router mounted and a default mock service.

    The default ``AsyncMock`` override keeps the monkey-patched
    ``Provide[...]`` marker from triggering eager DI resolution; individual
    tests swap in their own behaviour.
    """
    app = FastAPI()
    app.include_router(router)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: AsyncMock()
    with TestClient(app) as c:
        yield c


def _repair_url(device_uuid: str = "DEV-1") -> str:
    # ``tenant`` is a required query parameter on the endpoint.
    return f"/api/v1/admin/devices/{device_uuid}/repair?tenant=acme"


def _get_endpoint_dep_callable(router_, *, path_contains: str):
    """Extract the Provide[...] callable for the admin route matching the path."""
    for r in iter_api_routes(router_):
        if path_contains in r.path:
            for dep in r.dependant.dependencies:
                return dep.call
    raise RuntimeError(f"route containing {path_contains!r} not found")


# ============== Route path ==============


class TestDeviceRepairRoute:
    """The endpoint is published as /repair, not the old /status."""

    def test_repair_route_registered(self):
        paths = {r.path for r in iter_api_routes(router)}
        assert f"/api/v1/admin{REPAIR_PATH.removeprefix('/api/v1/admin')}" in paths

    def test_old_status_route_is_gone(self):
        paths = {r.path for r in iter_api_routes(router)}
        assert "/api/v1/admin/devices/{device_uuid}/status" not in paths


# ============== UpdateDeviceStatusRequest Model ==============


class TestUpdateDeviceStatusRequest:
    """UpdateDeviceStatusRequest input validation."""

    def test_status_only_is_valid(self):
        req = UpdateDeviceStatusRequest(status=DeviceStatus.ACTIVE, operator="ops")
        assert req.status is DeviceStatus.ACTIVE
        assert req.provider_device_id is None

    def test_provider_device_id_only_is_valid(self):
        req = UpdateDeviceStatusRequest(provider_device_id="sb-1", operator="ops")
        assert req.status is None
        assert req.provider_device_id == "sb-1"

    def test_both_fields_is_valid(self):
        req = UpdateDeviceStatusRequest(
            status="FAILED", provider_device_id="sb-1", operator="ops"
        )
        assert req.status is DeviceStatus.FAILED

    def test_neither_field_returns_422(self, client):
        resp = client.post(_repair_url(), json={"operator": "ops.alice"})
        assert resp.status_code == 422
        assert "at least one of" in resp.text

    def test_lowercase_status_returns_422(self, client):
        resp = client.post(_repair_url(), json={"status": "active", "operator": "ops"})
        assert resp.status_code == 422

    def test_unknown_status_returns_422(self, client):
        resp = client.post(_repair_url(), json={"status": "BOGUS", "operator": "ops"})
        assert resp.status_code == 422

    def test_missing_operator_returns_422(self, client):
        resp = client.post(_repair_url(), json={"status": "ACTIVE"})
        assert resp.status_code == 422

    def test_empty_provider_device_id_returns_422(self, client):
        resp = client.post(
            _repair_url(), json={"provider_device_id": "", "operator": "o"}
        )
        assert resp.status_code == 422

    def test_provider_device_id_too_long_returns_422(self, client):
        resp = client.post(
            _repair_url(), json={"provider_device_id": "x" * 129, "operator": "ops"}
        )
        assert resp.status_code == 422

    def test_provider_device_id_boundary_max_len_ok(self, client):
        provider_id = "x" * 128
        mock_svc = AsyncMock()
        mock_svc.update_device_status.return_value = UpdateDeviceStatusResult(
            device_uuid="DEV-1",
            previous_status="PENDING",
            new_status="PENDING",
            previous_provider_device_id=None,
            new_provider_device_id=provider_id,
        )
        dep = _get_endpoint_dep_callable(client.app.router, path_contains=REPAIR_PATH)
        client.app.dependency_overrides[dep] = lambda: mock_svc
        try:
            resp = client.post(
                _repair_url(),
                json={"provider_device_id": provider_id, "operator": "ops"},
            )
        finally:
            del client.app.dependency_overrides[dep]

        assert resp.status_code == 200


# ============== Endpoint behaviour ==============


class TestUpdateDeviceStatusEndpoint:
    """update_device_status_endpoint success and error paths."""

    def _post(self, client, payload, *, service):
        dep = _get_endpoint_dep_callable(client.app.router, path_contains=REPAIR_PATH)
        client.app.dependency_overrides[dep] = lambda: service
        try:
            return client.post(_repair_url(), json=payload)
        finally:
            del client.app.dependency_overrides[dep]

    def test_success_maps_envelope_and_forwards_fields(self, client):
        mock_svc = AsyncMock()
        mock_svc.update_device_status.return_value = UpdateDeviceStatusResult(
            device_uuid="DEV-1",
            previous_status="PENDING",
            new_status="ACTIVE",
            previous_provider_device_id="sb-old",
            new_provider_device_id="sb-new",
        )

        resp = self._post(
            client,
            {
                "status": "ACTIVE",
                "provider_device_id": "sb-new",
                "operator": "ops.alice",
            },
            service=mock_svc,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {
            "device_uuid": "DEV-1",
            "previous_status": "PENDING",
            "new_status": "ACTIVE",
            "previous_provider_device_id": "sb-old",
            "new_provider_device_id": "sb-new",
        }
        mock_svc.update_device_status.assert_awaited_once()
        kwargs = mock_svc.update_device_status.await_args.kwargs
        assert kwargs["device_uuid"] == "DEV-1"
        assert kwargs["status"] is DeviceStatus.ACTIVE
        assert kwargs["provider_device_id"] == "sb-new"
        assert kwargs["operator"] == "ops.alice"

    def test_provider_only_forwards_none_status(self, client):
        mock_svc = AsyncMock()
        mock_svc.update_device_status.return_value = UpdateDeviceStatusResult(
            device_uuid="DEV-1",
            previous_status="ACTIVE",
            new_status="ACTIVE",
            previous_provider_device_id=None,
            new_provider_device_id="sb-1",
        )

        resp = self._post(
            client, {"provider_device_id": "sb-1", "operator": "ops"}, service=mock_svc
        )

        assert resp.status_code == 200
        kwargs = mock_svc.update_device_status.await_args.kwargs
        assert kwargs["status"] is None
        assert kwargs["provider_device_id"] == "sb-1"

    def test_not_found_returns_404(self, client):
        mock_svc = AsyncMock()
        mock_svc.update_device_status.side_effect = PublishNotFoundError(
            "Device DEV-1 not found"
        )

        resp = self._post(
            client, {"status": "ACTIVE", "operator": "ops"}, service=mock_svc
        )

        assert resp.status_code == 404
        assert "DEV-1" in resp.json()["detail"]


# ============== Response model / helper ==============


class TestUpdateDeviceStatusResponse:
    """UpdateDeviceStatusResponse model and _to_update_status_response helper."""

    def test_response_construction(self):
        resp = UpdateDeviceStatusResponse(
            device_uuid="DEV-1",
            previous_status="PENDING",
            new_status="ACTIVE",
            previous_provider_device_id="sb-old",
            new_provider_device_id="sb-new",
        )
        assert resp.device_uuid == "DEV-1"
        assert resp.previous_provider_device_id == "sb-old"
        assert resp.new_provider_device_id == "sb-new"

    def test_helper_maps_all_fields(self):
        result = UpdateDeviceStatusResult(
            device_uuid="DEV-2",
            previous_status="FAILED",
            new_status="ACTIVE",
            previous_provider_device_id=None,
            new_provider_device_id="sb-9",
        )

        resp = _to_update_status_response(result)

        assert resp.device_uuid == "DEV-2"
        assert resp.previous_status == "FAILED"
        assert resp.new_status == "ACTIVE"
        assert resp.previous_provider_device_id is None
        assert resp.new_provider_device_id == "sb-9"
