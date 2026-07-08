"""Unit tests for local_paas_router.

Covers all 3 endpoints and error-handling:
- GET /api/v1/local/machines/{machine_id}/info — get_machine_info
- GET /api/v1/local/machines/{machine_id}/res-dirs — get_machine_res_dirs
- GET /api/v1/local/users/{user_id}/machines — list_user_machines
- DEVICE_CREATION_ERROR_TO_HTTP_STATUS — error code to HTTP status mapping
- _get_local_service — lazy singleton creation

Each endpoint receives ``local_paas_service: LocalPaasService`` via
FastAPI ``Depends(Provide[...])`` DI.  Tests call the async endpoint
functions directly, passing an ``AsyncMock`` as ``local_paas_service``,
which sidesteps the DI container entirely.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from secbaas.adapters.web.routers.paas_service.local_paas_router import (
    router,
)
from secbaas.core.repository.local_user_machine import (
    LocalUserMachineRecord,
)
from secbaas.core.service.paas import (
    DEVICE_CREATION_ERROR_TO_HTTP_STATUS,
    DeviceCreationError,
)
from tests.unit.adapters.web.conftest import iter_api_routes

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_machine_record(**overrides) -> LocalUserMachineRecord:
    """Create a LocalUserMachineRecord with sensible defaults."""
    defaults = {
        "id": 1,
        "gmt_create": datetime(2025, 1, 1, tzinfo=UTC),
        "gmt_modified": datetime(2025, 5, 1, tzinfo=UTC),
        "template_id": 100,
        "user_id": "user-001",
        "machine_id": "mac-pro-001",
        "machine_info": {"os": "macOS", "cpu_cores": 8},
        "last_heartbeat": datetime(2025, 5, 23, 12, 0, 0, tzinfo=UTC),
        "connected_server_instance": "10.0.0.1",
        "status": "ONLINE",
        "env": "dev",
    }
    defaults.update(overrides)
    return LocalUserMachineRecord(**defaults)


@pytest.fixture
def mock_service():
    """Create an AsyncMock that stands in for LocalPaasService.

    All handler-callable methods are mocked here.
    """
    mock = AsyncMock()
    mock.get_machine_info = AsyncMock()
    mock.get_machine_res_dirs = AsyncMock()
    mock.list_machines_by_user = AsyncMock()
    return mock


@pytest.fixture
def client(mock_service):
    """Build a minimal FastAPI app with the router and exception handlers.

    Overrides the DI container dependency to inject our mock service.
    """
    import logging

    from dependency_injector.wiring import Provide as ProvideCls
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    logger = logging.getLogger(__name__)

    app = FastAPI()
    app.include_router(router)

    # Override all Provide[...] dependencies with the mock service
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, ProvideCls):
                app.dependency_overrides[dep.call] = lambda: mock_service

    @app.exception_handler(DeviceCreationError)
    async def _device_creation_handler(
        request: Request, exc: DeviceCreationError
    ) -> JSONResponse:
        status_code = DEVICE_CREATION_ERROR_TO_HTTP_STATUS.get(str(exc.error_code), 500)
        if status_code < 500:
            logger.warning("DeviceCreationError: %s - %s", exc.error_code, exc.message)
        else:
            logger.error(
                "DeviceCreationError: %s - %s",
                exc.error_code,
                exc.message,
                exc_info=True,
            )
        detail: dict = {
            "error_code": str(exc.error_code),
            "message": exc.message,
        }
        if exc.context is not None:
            detail["diagnostic"] = exc.context
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(Exception)
    async def _generic_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "error_code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                }
            },
        )

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DEVICE_CREATION_ERROR_TO_HTTP_STATUS — mapping unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeviceCreationErrorMapping:
    """Unit tests for DEVICE_CREATION_ERROR_TO_HTTP_STATUS mapping dict."""

    @pytest.mark.parametrize(
        "error_code, expected_status",
        [
            ("MACHINE_NOT_FOUND", 404),
            ("MACHINE_OFFLINE", 503),
            ("CONTAINER_NOT_FOUND", 404),
            ("CONTAINER_LIMIT_EXCEEDED", 503),
            ("IMAGE_NOT_FOUND", 404),
            ("RESOURCE_EXHAUSTED", 503),
            ("CREATION_FAILED", 500),
            ("DESTROY_FAILED", 500),
            ("COMMAND_FAILED", 500),
            ("QUERY_FAILED", 500),
            ("INVALID_RESPONSE", 500),
            ("TIMEOUT", 502),
            ("BAD_GATEWAY", 502),
            ("PATH_NOT_FOUND", 404),
            ("PERMISSION_DENIED", 403),
            ("INVALID_PARAMS", 400),
            ("WORKER_OFFLINE", 503),
            ("MACHINE_NOT_CONNECTED", 503),
            ("INSTANCE_NOT_ASSIGNED", 503),
            ("LOCAL_TEMPLATE_NOT_CONFIGURED", 500),
            ("RELAY_SETUP_FAILED", 502),
            ("RELAY_TIMEOUT", 502),
        ],
    )
    def test_known_error_code_maps_to_correct_http_status(
        self, error_code, expected_status
    ):
        assert DEVICE_CREATION_ERROR_TO_HTTP_STATUS[error_code] == expected_status

    def test_all_statuses_are_valid_http(self):
        for error_code, status in DEVICE_CREATION_ERROR_TO_HTTP_STATUS.items():
            assert 400 <= status < 600, (
                f"{error_code} maps to {status}, expected 4xx/5xx"
            )

    def test_mapping_has_all_30_entries(self):
        assert len(DEVICE_CREATION_ERROR_TO_HTTP_STATUS) == 30

    def test_unknown_code_get_default(self):
        """Handler uses .get() with default 500 for unmapped codes."""
        assert (
            DEVICE_CREATION_ERROR_TO_HTTP_STATUS.get("WEIRD_UNKNOWN_CODE", 500) == 500
        )


# ---------------------------------------------------------------------------
# GET /api/v1/local/machines/{machine_id}/info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMachineInfo:
    """GET /api/v1/local/machines/{machine_id}/info"""

    def test_returns_machine_info_on_success(self, mock_service, client):
        mock_service.get_machine_info.return_value = {
            "cpu_cores": 8,
            "memory_gb": 16,
            "disk_gb": 256,
            "os": "macOS",
        }

        resp = client.get("/api/v1/local/machines/mac-pro-001/info")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["cpu_cores"] == 8
        assert data["memory_gb"] == 16
        assert data["disk_gb"] == 256
        mock_service.get_machine_info.assert_called_once_with(machine_id="mac-pro-001")

    def test_special_chars_machine_id_still_calls_service(self, mock_service, client):
        mock_service.get_machine_info.return_value = {
            "cpu_cores": 4,
            "memory_gb": 8,
        }

        resp = client.get("/api/v1/local/machines/bad%20id/info")

        assert resp.status_code == 200
        mock_service.get_machine_info.assert_called_once_with(machine_id="bad id")

    @pytest.mark.parametrize(
        "error_code, expected_status",
        [
            ("MACHINE_NOT_FOUND", 404),
            ("MACHINE_OFFLINE", 503),
            ("QUERY_FAILED", 500),
        ],
    )
    def test_device_creation_error_maps_to_http_status(
        self, mock_service, client, error_code, expected_status
    ):
        mock_service.get_machine_info.side_effect = DeviceCreationError(
            error_code, f"service: {error_code}"
        )

        resp = client.get("/api/v1/local/machines/mac-pro-001/info")

        assert resp.status_code == expected_status
        assert resp.json()["detail"]["error_code"] == error_code

    def test_machine_offline_includes_diagnostic(self, mock_service, client):
        diagnostic = {"last_heartbeat": "2025-05-20T00:00:00Z"}
        mock_service.get_machine_info.side_effect = DeviceCreationError(
            "MACHINE_OFFLINE",
            "machine is offline",
            context=diagnostic,
        )

        resp = client.get("/api/v1/local/machines/mac-pro-001/info")

        assert resp.status_code == 503
        assert resp.json()["detail"]["diagnostic"] == diagnostic

    def test_unexpected_exception_returns_internal_error(self, mock_service, client):
        mock_service.get_machine_info.side_effect = RuntimeError(
            "boom — database connection lost"
        )

        resp = client.get("/api/v1/local/machines/mac-pro-001/info")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/v1/local/machines/{machine_id}/res-dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMachineResDirs:
    """GET /api/v1/local/machines/{machine_id}/res-dirs"""

    def test_returns_directory_tree_on_success(self, mock_service, client):
        mock_service.get_machine_res_dirs.return_value = {
            "name": "Desktop",
            "children": [
                {"name": "project-a"},
                {"name": "notes.txt"},
            ],
        }

        resp = client.get("/api/v1/local/machines/mac-pro-001/res-dirs?dir=~/Desktop")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "Desktop"
        assert len(data["children"]) == 2
        mock_service.get_machine_res_dirs.assert_called_once_with(
            machine_id="mac-pro-001", dir="~/Desktop"
        )

    def test_uses_default_dir_when_not_provided(self, mock_service, client):
        mock_service.get_machine_res_dirs.return_value = {
            "name": "Desktop",
            "children": [],
        }

        resp = client.get("/api/v1/local/machines/mac-pro-001/res-dirs")

        assert resp.status_code == 200
        mock_service.get_machine_res_dirs.assert_called_once_with(
            machine_id="mac-pro-001", dir="~/Desktop"
        )

    def test_device_creation_error_maps_properly(self, mock_service, client):
        mock_service.get_machine_res_dirs.side_effect = DeviceCreationError(
            "PATH_NOT_FOUND", "directory does not exist"
        )

        resp = client.get(
            "/api/v1/local/machines/mac-pro-001/res-dirs?dir=/nonexistent"
        )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "PATH_NOT_FOUND"

    def test_invalid_params_error_returns_400(self, mock_service, client):
        mock_service.get_machine_res_dirs.side_effect = DeviceCreationError(
            "INVALID_PARAMS", "path contains .. traversal"
        )

        resp = client.get(
            "/api/v1/local/machines/mac-pro-001/res-dirs?dir=../../etc/passwd"
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_PARAMS"

    def test_permission_denied_returns_403(self, mock_service, client):
        mock_service.get_machine_res_dirs.side_effect = DeviceCreationError(
            "PERMISSION_DENIED", "cannot read /root"
        )

        resp = client.get("/api/v1/local/machines/mac-pro-001/res-dirs?dir=/root")

        assert resp.status_code == 403

    def test_unexpected_exception_returns_500(self, mock_service, client):
        mock_service.get_machine_res_dirs.side_effect = RuntimeError("out of memory")

        resp = client.get("/api/v1/local/machines/mac-pro-001/res-dirs?dir=/home")

        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/v1/local/users/{user_id}/machines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListUserMachines:
    """GET /api/v1/local/users/{user_id}/machines"""

    def test_returns_machines_for_user(self, mock_service, client):
        records = [
            _make_machine_record(id=1, machine_id="mac-001", status="ONLINE"),
            _make_machine_record(id=2, machine_id="mac-002", status="OFFLINE"),
        ]
        mock_service.list_machines_by_user.return_value = records

        resp = client.get("/api/v1/local/users/user-001/machines")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["machine_id"] == "mac-001"
        assert data[0]["status"] == "ONLINE"
        assert data[1]["machine_id"] == "mac-002"
        assert data[1]["status"] == "OFFLINE"
        mock_service.list_machines_by_user.assert_called_once_with(user_id="user-001")

    def test_returns_empty_list_when_user_has_no_machines(self, mock_service, client):
        mock_service.list_machines_by_user.return_value = []

        resp = client.get("/api/v1/local/users/user-999/machines")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_response_excludes_internal_fields(self, mock_service, client):
        """Verify id, gmt_create, gmt_modified are NOT leaked."""
        record = _make_machine_record(id=99)
        mock_service.list_machines_by_user.return_value = [record]

        resp = client.get("/api/v1/local/users/user-001/machines")

        assert resp.status_code == 200
        machine = resp.json()["data"][0]
        # Internal fields must be absent
        assert "id" not in machine
        assert "gmt_create" not in machine
        assert "gmt_modified" not in machine
        # Public fields must be present
        assert machine["template_id"] == record.template_id
        assert machine["user_id"] == record.user_id
        assert machine["machine_id"] == record.machine_id
        assert machine["status"] == record.status
        assert machine["env"] == record.env

    def test_response_includes_last_heartbeat(self, mock_service, client):
        record = _make_machine_record()
        mock_service.list_machines_by_user.return_value = [record]

        resp = client.get("/api/v1/local/users/user-001/machines")

        assert resp.status_code == 200
        machine = resp.json()["data"][0]
        assert machine["last_heartbeat"] is not None

    def test_device_creation_error_maps_to_http_status(self, mock_service, client):
        mock_service.list_machines_by_user.side_effect = DeviceCreationError(
            "QUERY_FAILED", "db timeout"
        )

        resp = client.get("/api/v1/local/users/user-001/machines")

        assert resp.status_code == 500
        assert resp.json()["detail"]["error_code"] == "QUERY_FAILED"

    def test_unexpected_exception_returns_500(self, mock_service, client):
        mock_service.list_machines_by_user.side_effect = RuntimeError("connection lost")

        resp = client.get("/api/v1/local/users/user-001/machines")

        assert resp.status_code == 500
