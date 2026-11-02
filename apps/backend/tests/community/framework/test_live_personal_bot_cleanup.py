"""Acceptance runtimes are released even when startup or later assertions fail."""
from __future__ import annotations

import httpx
import pytest

from tests.community.acceptance._fixtures import live_personal_bot as helpers


@pytest.mark.parametrize("startup_fails,replacement", [(True, 7), (False, 8)])
def test_cleanup_releases_failed_startup_and_reactivated_device(monkeypatch, startup_fails, replacement):
    released = []
    finalizers = []

    class Request:
        addfinalizer = staticmethod(finalizers.append)

    def handle(request):
        path = request.url.path
        if request.method == "POST" and path == "/api/bots":
            data = {"bot": {"bot_id": "acceptance-bot", "binding_id": 7}}
        elif path == "/api/bots/acceptance-bot":
            data = {"binding_id": replacement}
        elif request.method == "POST" and path.endswith("/release"):
            assert request.headers["x-user-id"] == "acceptance-owner"
            released.append(int(path.split("/")[-2]))
            data = {"status": "RELEASED"}
        else:
            assert path in ("/api/v1/devices/7", "/api/v1/devices/8")
            data = {"status": "RELEASED" if replacement == 8 and path.endswith("/7") else "FAILED"}
        return httpx.Response(200, json={"success": True, "data": data})

    transport = httpx.MockTransport(handle)
    client_type = httpx.Client
    monkeypatch.setattr(helpers.httpx, "Client", lambda **kwargs: client_type(transport=transport, **kwargs))

    def ready(*_args):
        assert len(finalizers) == 1, "Cleanup must be registered before readiness"
        if startup_fails:
            raise AssertionError("publish failed")
        return {"device_provider": "baas"}

    monkeypatch.setattr(helpers, "wait_bot_ready", ready)
    with client_type(base_url="http://acceptance.test", transport=transport) as client:
        if startup_fails:
            with pytest.raises(AssertionError, match="publish failed"):
                helpers.create_live_personal_bot(client, request=Request(), user_id="acceptance-owner", bot_name_prefix="test")
        else:
            helpers.create_live_personal_bot(client, request=Request(), user_id="acceptance-owner", bot_name_prefix="test")
    # Pytest runs cleanup after the test's HTTP client has closed.
    finalizers[0]()
    assert released == [replacement]
