"""TDD for the ExecutionPort httpx client (Phase 4.2, plan §4.2).

Real engine/BCS endpoints are TODO (R6/B5 not in the open-source repo). Tests
use a fake httpx Client to verify the call shape + DispatchResult contract.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.task.protocols import DispatchResult
from agentclaw.community.core.task.domain.models import RunMode
from agentclaw.community.plugins.community.task.execution_port_client import (
    ExecutionPortClient,
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
    def __init__(self, payload=None) -> None:
        self.posts: list[tuple[str, dict]] = []
        self._payload = payload or {"run_id": "sm-42"}

    def post(self, url: str, json: dict):
        self.posts.append((url, json))
        return _FakeResponse(self._payload)

    def close(self) -> None:
        pass


def test_dispatch_single_bot_posts_to_engine_and_returns_token():
    fake = _FakeClient()
    client = ExecutionPortClient("http://engine", "http://bcs", client=fake)  # type: ignore[arg-type]
    r = client.dispatch_single_bot("t1", "n1", "bot-a")
    assert isinstance(r, DispatchResult)
    assert r.executor_id == "bot-a"
    assert r.run_mode is RunMode.SINGLE_BOT
    assert r.accept_token.startswith("tok-")
    url, body = fake.posts[0]
    assert url == "http://engine/api/tasks/dispatch"
    assert body["bot_id"] == "bot-a"


def test_coop_group_returns_run_id_from_bcs():
    client = ExecutionPortClient("http://engine", "http://bcs", client=_FakeClient({"run_id": "sm-42"}))  # type: ignore[arg-type]
    r = client.coop_group("t1", "n1", ["b1", "b2"])
    assert r.run_mode is RunMode.COOP_GROUP
    assert r.dispatched_at == "sm-42"


def test_redispatch_node_posts_to_bcs():
    fake = _FakeClient()
    client = ExecutionPortClient("http://engine", "http://bcs", client=fake)  # type: ignore[arg-type]
    client.redispatch_node("t1", "n1", "bot-r")
    url, body = fake.posts[0]
    assert url == "http://bcs/api/coop-groups/redispatch"
    assert body["bot_id"] == "bot-r"


def test_bbs_returns_bbs_dispatch_result_no_http():
    fake = _FakeClient()
    client = ExecutionPortClient("http://engine", "http://bcs", client=fake)  # type: ignore[arg-type]
    r = client.bbs("t1", "n1", "stuck")
    assert r.run_mode is RunMode.BBS
    assert fake.posts == []  # bbs is a Phase 5 stub, no http yet


def test_probe_posts_to_engine_and_returns_ack():
    """6.5: watchdog PROBE pings the engine's probe endpoint (ask the bot to
    report status) and returns a SINGLE_BOT ack DispatchResult."""
    fake = _FakeClient()
    client = ExecutionPortClient("http://engine", "http://bcs", client=fake)  # type: ignore[arg-type]
    r = client.probe("t1", "n1", "bot-a")
    assert isinstance(r, DispatchResult)
    assert r.executor_id == "bot-a"
    assert r.run_mode is RunMode.SINGLE_BOT
    assert r.accept_token.startswith("tok-")
    assert len(fake.posts) == 1
    url, body = fake.posts[0]
    assert url == "http://engine/api/tasks/probe"
    assert body == {"task_id": "t1", "node_id": "n1", "bot_id": "bot-a"}


def test_http_error_raises():
    class _ErrClient(_FakeClient):
        def post(self, url, json):
            return _FakeResponse({}, status_code=500)

    client = ExecutionPortClient("http://engine", "http://bcs", client=_ErrClient())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        client.dispatch_single_bot("t1", "n1", "bot-a")