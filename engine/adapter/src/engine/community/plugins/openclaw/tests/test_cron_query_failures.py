"""Exercise the real OpenClaw cron query through the HTTP boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.cron.router import router, set_cron_api
from engine.community.core.adapters.openclaw.cron import OpenClawCronAdapter
from engine.community.openclaw.protocol import ResponseFrame
from engine.community.plugins.openclaw._cron import _CronPortMixin


@pytest.fixture
def query():
    port = _CronPortMixin()
    client = SimpleNamespace(hello=None, send_request=AsyncMock())
    port._pooled_client = AsyncMock(return_value=client)
    set_cron_api(OpenClawCronAdapter(port))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as http:
        yield port, client, http
    set_cron_api(None)


@pytest.mark.parametrize("error", [ConnectionError("Not connected"), TimeoutError("timeout")])
def test_transport_failure_is_not_task_not_found(query, error):
    port, client, http = query
    client.send_request.side_effect = error
    response = http.get("/api/cron/job-1")
    assert response.status_code == 500
    assert response.json()["detail"] == str(error)
    port._pooled_client.assert_awaited()
    client.send_request.assert_awaited_once_with("cron.list", {"includeDisabled": True})


@pytest.mark.parametrize("payload", [None, {}, {"jobs": None}, {"jobs": "invalid"}, [None]])
def test_malformed_list_is_not_task_not_found(query, payload):
    _, client, http = query
    client.send_request.return_value = ResponseFrame(id="test", ok=True, payload=payload)
    response = http.get("/api/cron/job-1")
    assert response.status_code == 500
    assert "invalid job list" in response.json()["detail"]


def test_rpc_error_is_not_task_not_found(query):
    _, client, http = query
    client.send_request.return_value = ResponseFrame(id="test", ok=False)
    response = http.get("/api/cron/job-1")
    assert response.status_code == 500
    assert "Failed to list jobs" in response.json()["detail"]


@pytest.mark.parametrize("payload", [[], {"jobs": []}, {"entries": []}])
def test_successful_empty_list_still_returns_404(query, payload):
    _, client, http = query
    client.send_request.return_value = ResponseFrame(id="test", ok=True, payload=payload)
    response = http.get("/api/cron/job-1")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


def test_cron_get_failure_and_fallback_failure_do_not_become_404(query):
    _, client, http = query
    client.hello = SimpleNamespace(features=SimpleNamespace(methods=["cron.get"]))
    client.send_request.side_effect = ConnectionError("Not connected")
    assert http.get("/api/cron/job-1").status_code == 500
    assert [call.args[0] for call in client.send_request.await_args_list] == [
        "cron.get", "cron.list",
    ]


def test_failed_query_can_recover_without_losing_existing_task(query):
    _, client, http = query
    client.send_request.side_effect = [
        ConnectionError("Not connected"),
        ResponseFrame(id="test", ok=True, payload={"jobs": [
            {"id": "job-1", "name": "Existing task", "schedule": {}, "payload": {}},
        ]}),
    ]
    assert http.get("/api/cron/job-1").status_code == 500
    response = http.get("/api/cron/job-1")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == "job-1"
