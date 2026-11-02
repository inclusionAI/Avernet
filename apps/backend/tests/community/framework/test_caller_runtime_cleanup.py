"""Caller runtime cleanup waits for publication and still releases failed starts."""
from __future__ import annotations

import json

import httpx
import pytest

from tests.community.acceptance.expert_chat import conftest as cleanup


@pytest.mark.parametrize("terminal", ["success", "failed"])
def test_caller_cleanup_settles_create_before_releasing(terminal, monkeypatch):
    statuses = iter(["init", terminal])
    calls = []
    sleeps = []
    monkeypatch.setattr(cleanup.time, "sleep", sleeps.append)

    def handle(request):
        if request.url.path.endswith("/caller-connection"):
            assert request.url.params["user_id"] == "caller"
            status = next(statuses)
            calls.append(status)
            data = {"instance": {"status": status}, "need_poll": status != "success"}
        else:
            assert request.headers["x-user-id"] == "owner"
            calls.append(request.method)
            data = {"status": "ACTIVE" if request.method == "GET" else "RELEASED"}
        return httpx.Response(200, json={"success": True, "data": data})

    instance = {"id": 4, "bot_id": "bot", "owner_id": "owner", "user_id": "caller",
                "ext": json.dumps({"binding_id": 9})}
    with httpx.Client(base_url="http://acceptance.test", transport=httpx.MockTransport(handle)) as client:
        if terminal == "failed":
            with pytest.raises(AssertionError, match="Caller runtime failed"):
                cleanup._release_instance(client, instance)
        else:
            cleanup._release_instance(client, instance)
    assert calls == ["init", terminal, "GET", "POST"]
    assert sleeps == [1]
