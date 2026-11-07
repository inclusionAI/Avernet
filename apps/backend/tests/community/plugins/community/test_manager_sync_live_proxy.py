"""Check fault-proxy semantics without mutating a real Backend or BCS."""
from types import SimpleNamespace

import httpx
import pytest

from tests.community.acceptance.bcs_manager_sync_external.test_bcs_manager_sync_live import (
    _FaultPlan, _start_fault_proxy,
)


@pytest.fixture
def proxy(monkeypatch):
    real_client = httpx.Client
    forwarded = []

    class Upstream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, path, **kwargs):
            forwarded.append((method, path, kwargs))
            return httpx.Response(200, json={
                "code": 20000, "request_id": "proxy-test",
                "data": {"added": 1, "removed": 0, "total": 1},
            })

    monkeypatch.setattr(httpx, "Client", Upstream)
    plan = _FaultPlan()
    config = SimpleNamespace(destructive_upstream_url="https://bcs.example.test", destructive_proxy_port=0)
    server = _start_fault_proxy(config, plan)
    try:
        with real_client(base_url=f"http://127.0.0.1:{server.server_port}", timeout=2) as client:
            yield plan, client, forwarded
    finally:
        server.shutdown()
        server.server_close()


def test_proxy_rejects_without_mutating_upstream(proxy):
    plan, client, forwarded = proxy
    plan.reject_status = 403
    response = client.put("/managers", json={"manager_user_ids": ["member"]})
    assert response.status_code == 403
    assert plan.count() == 1
    assert forwarded == []


def test_proxy_disconnect_is_finite_and_next_request_can_succeed(proxy):
    plan, client, forwarded = proxy
    plan.disconnects_remaining = 1
    with pytest.raises(httpx.RemoteProtocolError):
        client.put("/managers", json={"manager_user_ids": ["member"]})
    assert forwarded == []
    assert client.put("/managers", json={"manager_user_ids": []}).status_code == 200
    assert len(forwarded) == 1
    assert plan.count() == 2


def test_proxy_count_rewrite_does_not_suppress_real_write(proxy):
    plan, client, forwarded = proxy
    plan.rewrite_total = 0
    response = client.put("/managers", json={"manager_user_ids": ["member"]})
    assert response.json()["data"]["total"] == 0
    assert len(forwarded) == 1
