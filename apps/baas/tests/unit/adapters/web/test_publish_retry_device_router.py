"""Contract tests for POST /api/v1/publishes/{publish_id}/devices/{device_uuid}/retry.

The service raises ``ValueError`` with a ``CODE: detail`` prefix for every
precondition violation; the route maps that prefix to the HTTP status. These
tests pin the mapping and the success shape so the wire contract cannot drift
from the service.
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
    # Replace every Provide[...] dependency with the test double.
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: svc
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


def _url(publish_id: int = 105597, device_uuid: str = "DEVICE-abc") -> str:
    return f"/api/v1/publishes/{publish_id}/devices/{device_uuid}/retry"


def _body(**overrides) -> dict:
    payload = {
        "operator": "langlang.cai",
        "request_id": VALID_REQUEST_ID,
    }
    payload.update(overrides)
    return payload


class TestRetryDeviceSuccess:
    def test_returns_processing_summary(self, client, svc):
        svc.retry_device.return_value = {
            "publish_id": 105597,
            "device_uuid": "DEVICE-abc",
            "record_id": 107744,
            "result_status": "PROCESSING",
            "publish_manual_retry_count": 1,
            "max_manual_retries": 3,
        }

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["result_status"] == "PROCESSING"
        assert data["publish_manual_retry_count"] == 1
        svc.retry_device.assert_awaited_once()
        kwargs = svc.retry_device.call_args.kwargs
        assert kwargs["publish_id"] == 105597
        assert kwargs["device_uuid"] == "DEVICE-abc"
        assert kwargs["operator"] == "langlang.cai"
        # Default budget stays independent of the auto-retry budget.
        assert kwargs["max_manual_retries"] == 3

    def test_forwards_custom_budget(self, client, svc):
        svc.retry_device.return_value = {"result_status": "PROCESSING"}

        resp = client.post(
            _url(),
            params={"tenant": "team_claw"},
            json=_body(max_manual_retries=5),
        )

        assert resp.status_code == 200
        assert svc.retry_device.call_args.kwargs["max_manual_retries"] == 5


class TestRetryDeviceErrorMapping:
    @pytest.mark.parametrize(
        "error, expected_status, expected_code",
        [
            ("PUBLISH_NOT_FOUND: publish_id=1", 404, "PUBLISH_NOT_FOUND"),
            ("DEVICE_NOT_IN_PUBLISH: device_uuid=x", 404, "DEVICE_NOT_IN_PUBLISH"),
            ("PUBLISH_TERMINAL: is SUCCESS", 409, "PUBLISH_TERMINAL"),
            ("RECORD_NOT_FAILED: is SUCCESS", 409, "RECORD_NOT_FAILED"),
            (
                "PROVIDER_RETRY_UNSUPPORTED: not ARCA",
                409,
                "PROVIDER_RETRY_UNSUPPORTED",
            ),
            ("MANUAL_RETRY_EXHAUSTED: limit=3", 409, "MANUAL_RETRY_EXHAUSTED"),
        ],
    )
    def test_error_code_maps_to_status(
        self, client, svc, error, expected_status, expected_code
    ):
        svc.retry_device.side_effect = ValueError(error)

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == expected_status
        assert resp.json()["detail"]["error_code"] == expected_code


class TestRetryDeviceValidation:
    def test_missing_operator_is_rejected(self, client, svc):
        resp = client.post(
            _url(),
            params={"tenant": "team_claw"},
            json={"request_id": VALID_REQUEST_ID},
        )

        assert resp.status_code == 422
        svc.retry_device.assert_not_awaited()

    def test_out_of_range_budget_is_rejected(self, client, svc):
        resp = client.post(
            _url(),
            params={"tenant": "team_claw"},
            json=_body(max_manual_retries=99),
        )

        assert resp.status_code == 422
        svc.retry_device.assert_not_awaited()

    def test_unmapped_value_error_is_not_swallowed(self, client, svc):
        """An unknown error code must not be misreported as a client error."""
        svc.retry_device.side_effect = ValueError("SOMETHING_ELSE: exploded")

        resp = client.post(_url(), params={"tenant": "team_claw"}, json=_body())

        assert resp.status_code == 500
        assert "error_code" not in resp.text or "SOMETHING_ELSE" not in resp.text
