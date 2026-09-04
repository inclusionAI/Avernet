"""Endpoint tests for ``POST /openapi/v1/bots/{bot_id}/engine/restart``.

#80 engine-restart relays the device-side daemon's ``POST /api/engine/restart``
via ``EngineRuntimeRelay`` — the same path the legacy frontend reached through
the agentclawproxy proxypass. The handler owns no restart logic: it resolves
the addressed bot, checks the caller is its operator, forwards the call, and
publishes a coarse dispatch state.
"""
from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import router
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, fails, ok


@pytest.fixture
def client(make_client):
    return make_client(router)


def _restart(bot: str = BOT) -> str:
    return f"/openapi/v1/bots/{bot}/engine/restart"


def test_restart_forwards_to_the_engine_daemon(client, relay):
    relay.results = [EngineResult(data={"status": "restarting"})]

    data = ok(client.post(_restart()))

    assert relay.calls[0]["method"] == "POST"
    assert relay.calls[0]["path"] == "/api/engine/restart"
    # The result echoes the bot and the coarse dispatch state the daemon gave.
    assert data == {"bot_id": BOT, "status": "restarting"}


def test_restart_sends_the_body_the_engine_adapter_requires(client, relay):
    # The device endpoint is typed ``engine_restart(request:
    # EngineRestartRequest)`` — FastAPI answers 422 to a bodyless POST however
    # optional the model's fields are, and the relay flattens that 422 into a
    # public 502. The forward must always carry the JSON envelope, force off.
    relay.results = [EngineResult(data={"status": "restarting"})]

    ok(client.post(_restart()))

    assert relay.calls[0]["body"] == {"force": False}


def test_restart_status_falls_back_to_empty_when_engine_omits_it(client, relay):
    relay.results = [EngineResult(data={})]

    data = ok(client.post(_restart()))

    assert data == {"bot_id": BOT, "status": ""}


def test_restart_on_a_bot_not_owned_by_caller_does_not_reach_the_device(client, relay):
    # A foreign bot resolves to the same masked 404 an absent bot does — by
    # design, so a refused non-operator cannot probe. No forward happens.
    fails(client.post(_restart("other-bot")), 404)
    assert relay.calls == []


def test_restart_does_not_publish_a_made_up_legacy_address():
    """A new bot-first operation has no component-first contract to retire."""
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    paths = public_document()["paths"]

    assert "/openapi/v1/bots/{bot_id}/engine/restart" in paths
    assert "/openapi/v1/bots/engine/{bot_id}/restart" not in paths
