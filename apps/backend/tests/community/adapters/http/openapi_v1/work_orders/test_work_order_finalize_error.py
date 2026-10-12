"""Public finalization failure must not look like a failed remote callback."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.responses import envelope_errors
from agentclaw.community.core.work_orders.errors import WorkOrderLocalFinalizeError


def test_finalization_error_is_fixed_and_has_distinct_code():
    app = FastAPI()

    @app.get("/finalization")
    @envelope_errors
    async def finalize(request: Request):
        try:
            raise RuntimeError("private database contents")
        except RuntimeError as exc:
            raise WorkOrderLocalFinalizeError(123) from exc

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/finalization")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == 500201
    assert "External decision completed" in payload["message"]
    assert "Reconcile before retrying" in payload["message"]
    assert "private database contents" not in response.text


@pytest.mark.parametrize("order_id", [1, 99])
def test_domain_error_keeps_order_identity(order_id):
    error = WorkOrderLocalFinalizeError(order_id)
    assert error.work_order_id == order_id
