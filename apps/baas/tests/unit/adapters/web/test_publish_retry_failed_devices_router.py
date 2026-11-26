"""Contract tests for POST /api/v1/publishes/{publish_id}/devices/retry-failed.

The bulk route must not be shadowed by the single-device route
``devices/{device_uuid}/retry`` — a path-order regression would silently retry
a device literally named ``retry-failed``. These tests pin the precedence as
well as the response shape.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web.routers.bot_service.publish_router import (
    router,
)
from tests.unit.adapters.web.conftest import iter_api_routes

VALID_REQUEST_ID = "487ec32cf90b424195f6786651ac1ba5"


@pytest.fixture
def svc():
    return AsyncMock()


@pytest.fixture
def client(svc):
    app = FastAPI()
    app.include_router(router)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: svc
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


def _url(publish_id: int = 105597) -> str:
    return f"/api/v1/publishes/{publish_id}/devices/retry-failed"


def _body(**overrides) -> dict:
    payload = {
        "operator": "langlang.cai",
        "request_id": VALID_REQUEST_ID,
    }
    payload.update(overrides)
    return payload


class TestBulkRetrySuccess:
    def test_returns_per_device_outcome(self, client, svc):
        svc.retry_failed_devices.return_value = {
            "publish_id": 105597,
            "requested": 2,
            "accepted_count": 1,
            "skipped_count": 1,
            "accepted": [
                {
                    "record_id": 1,
                    "device_uuid": "DEVICE-a",
                    "publish_manual_retry_count": 1,
                }
            ],
            "skipped": [
                {
                    "record_id": 2,
                    "device_uuid": "DEVICE-b",
                    "error_code": "MANUAL_RETRY_EXHAUSTED",
                    "message": "limit=3",
                }
            ],
            "max_manual_retries": 3,
        }

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["accepted_count"] == 1
        assert data["skipped_count"] == 1
        assert data["skipped"][0]["error_code"] == "MANUAL_RETRY_EXHAUSTED"
        svc.retry_failed_devices.assert_awaited_once()
        kwargs = svc.retry_failed_devices.call_args.kwargs
        assert kwargs["publish_id"] == 105597
        assert kwargs["max_manual_retries"] == 3

    def test_bulk_route_is_not_shadowed_by_single_device_route(self, client, svc):
        """`devices/retry-failed` must hit the bulk handler, not `{device_uuid}`."""
        svc.retry_failed_devices.return_value = {"accepted_count": 0}

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == 200
        svc.retry_failed_devices.assert_awaited_once()
        svc.retry_device.assert_not_awaited()

    def test_forwards_custom_budget(self, client, svc):
        svc.retry_failed_devices.return_value = {"accepted_count": 0}

        resp = client.post(
            _url(),
            params={"tenant": "team_claw"},
            json=_body(max_manual_retries=7),
        )

        assert resp.status_code == 200
        assert svc.retry_failed_devices.call_args.kwargs["max_manual_retries"] == 7


class TestBulkRetryErrorMapping:
    @pytest.mark.parametrize(
        "error, expected_status, expected_code",
        [
            ("PUBLISH_NOT_FOUND: publish_id=1", 404, "PUBLISH_NOT_FOUND"),
            ("PUBLISH_TERMINAL: is SUCCESS", 409, "PUBLISH_TERMINAL"),
        ],
    )
    def test_error_code_maps_to_status(
        self, client, svc, error, expected_status, expected_code
    ):
        svc.retry_failed_devices.side_effect = ValueError(error)

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == expected_status
        assert resp.json()["detail"]["error_code"] == expected_code

    def test_unmapped_value_error_is_not_swallowed(self, client, svc):
        svc.retry_failed_devices.side_effect = ValueError("SOMETHING_ELSE: boom")

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == 500
