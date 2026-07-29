"""TDD for the BCS collaboration httpx client (Phase 4.2a, plan §2.4).

Read-only query face against the local open-source BCS. Uses a fake httpx
``Client`` (no real network) — asserts endpoint paths, JSON parsing, 404 → {}.
"""
from __future__ import annotations

from agentclaw.community.plugins.community.task.bcs_collaboration_client import (
    BcsCollaborationClient,
)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, routes: dict) -> None:
        # path → _FakeResponse
        self._routes = routes
        self.calls: list = []

    def get(self, path: str):
        self.calls.append(path)
        return self._routes.get(path, _FakeResponse({}, status_code=404))

    def close(self) -> None:
        pass


def test_fetch_state_machine_run_graph_hits_graph_endpoint():
    fake = _FakeClient({
        "/state-machine-runs/sm-1/graph": _FakeResponse({"run": {"run_id": "sm-1"}, "nodes": []}),
    })
    client = BcsCollaborationClient("http://bcs.local", client=fake)  # type: ignore[arg-type]
    snap = client.fetch_state_machine_run_graph("sm-1")
    assert snap == {"run": {"run_id": "sm-1"}, "nodes": []}
    assert fake.calls == ["/state-machine-runs/sm-1/graph"]


def test_fetch_node_detail_hits_node_endpoint():
    fake = _FakeClient({
        "/state-machine-runs/sm-1/nodes/n9": _FakeResponse({"node": {"node_id": "n9"}, "judge_outputs": []}),
    })
    client = BcsCollaborationClient("http://bcs.local", client=fake)  # type: ignore[arg-type]
    detail = client.fetch_node_detail("sm-1", "n9")
    assert detail["node"]["node_id"] == "n9"
    assert fake.calls == ["/state-machine-runs/sm-1/nodes/n9"]


def test_404_returns_empty_dict_not_raise():
    fake = _FakeClient({})  # any path → default 404
    client = BcsCollaborationClient("http://bcs.local", client=fake)  # type: ignore[arg-type]
    assert client.fetch_state_machine_run_graph("missing") == {}


def test_non_404_error_raises():
    fake = _FakeClient({
        "/state-machine-runs/sm-1/graph": _FakeResponse({}, status_code=500),
    })
    client = BcsCollaborationClient("http://bcs.local", client=fake)  # type: ignore[arg-type]
    try:
        client.fetch_state_machine_run_graph("sm-1")
        assert False, "expected raise"
    except RuntimeError:
        pass