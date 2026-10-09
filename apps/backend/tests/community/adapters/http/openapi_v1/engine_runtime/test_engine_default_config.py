"""Endpoint tests for ``GET /openapi/v1/bots/{bot_id}/engine/default-config``.

The route relays the engine adapter's ``GET /api/openclaw/default-config`` via
``EngineRuntimeRelay`` — the same read the legacy console made through the
agentclawproxy proxypass to offer "restore defaults". The handler owns no
logic: resolve the addressed bot, check the caller is its operator, forward,
and publish the engine's default configuration — the ``path`` the engine
answers alongside it stays behind the boundary.
"""
from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import router
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
)
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, fails, ok


@pytest.fixture
def client(make_client):
    return make_client(router)


def _default_config(bot: str = BOT) -> str:
    return f"/openapi/v1/bots/{bot}/engine/default-config"


_ENGINE_ANSWER = {
    "path": "local://default-config",
    "config": {"model": "claude-sonnet-5", "permissions": {"allow": []}},
}


def test_default_config_forwards_to_the_engine_adapter(client, relay):
    relay.results = [EngineResult(data=_ENGINE_ANSWER)]

    data = ok(client.get(_default_config()))

    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["path"] == "/api/openclaw/default-config"
    assert data == {"config": _ENGINE_ANSWER["config"]}


def test_default_config_does_not_publish_the_engine_file_path(client, relay):
    # The engine answers ``{"path": ..., "config": ...}``; the path names a
    # file inside the device and is not actionable by a caller, so it must not
    # cross the boundary even though it is in the payload.
    relay.results = [EngineResult(data=_ENGINE_ANSWER)]

    data = ok(client.get(_default_config()))

    assert "path" not in data
    assert data["config"]["model"] == "claude-sonnet-5"


def test_default_config_config_falls_back_to_empty_when_engine_omits_it(client, relay):
    relay.results = [EngineResult(data={"path": "local://default-config"})]

    data = ok(client.get(_default_config()))

    assert data == {"config": {}}


def test_default_config_config_non_dict_answers_empty_not_an_error(client, relay):
    # Free-form JSON that is not an object reads as "no defaults to publish"
    # rather than failing the whole read.
    relay.results = [EngineResult(data={"config": "not-a-mapping"})]

    data = ok(client.get(_default_config()))

    assert data == {"config": {}}


def test_default_config_on_an_engine_without_the_capability_answers_501(client, relay):
    relay.raises = EngineCapabilityUnsupportedError(
        "engine does not support default-config"
    )

    fails(client.get(_default_config()), 501)


def test_default_config_on_a_bot_not_owned_by_caller_does_not_reach_the_device(
    client, relay
):
    # A foreign bot resolves to the same masked 404 an absent bot does — by
    # design, so a refused non-operator cannot probe. No forward happens.
    fails(client.get(_default_config("other-bot")), 404)
    assert relay.calls == []


def test_default_config_does_not_publish_a_made_up_legacy_address():
    """A new bot-first operation has no component-first contract to retire."""
    from tests.community.adapters.http.openapi_v1.conftest import public_document

    paths = public_document()["paths"]

    assert "/openapi/v1/bots/{bot_id}/engine/default-config" in paths
    assert "/openapi/v1/bots/engine/{bot_id}/default-config" not in paths