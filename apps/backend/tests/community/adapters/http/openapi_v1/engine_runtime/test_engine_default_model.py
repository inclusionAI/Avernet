"""Default-model projection, isolation and transport failure contract."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import router
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineStageNotLiveError,
)
from agentclaw.community.core.engine_runtime.models import EngineResult
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTimeoutError,
)
from .conftest import BOT, fails, ok


@pytest.fixture
def client(make_client):
    return make_client(router)


def test_default_model_forwards_exact_target_and_projects_only_id(client, relay):
    relay.results = [
        EngineResult(data={"id": "provider/model", "config": {"secret": "hidden"}})
    ]
    data = ok(client.get(f"/openapi/v1/bots/{BOT}/engine/default-model?device_uuid=d1"))
    assert data == {"model_id": "provider/model"}
    assert len(relay.calls) == 1
    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["path"] == "/api/engine/default-model"
    assert relay.calls[0]["stage"] == "draft"
    assert relay.calls[0]["device_uuid"] == "d1"
    assert relay.calls[0]["body"] is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"id": None},
        {"id": 42},
        {"id": ""},
        {"id": " "},
        {"id": " model"},
        {"id": "m\n"},
    ],
)
def test_invalid_upstream_is_not_a_successful_empty_default(client, relay, payload):
    relay.results = [EngineResult(data=payload)]
    fails(client.get(f"/openapi/v1/bots/{BOT}/engine/default-model"), 502)


@pytest.mark.parametrize(
    "error,status",
    [
        (EngineCapabilityUnsupportedError("unsupported"), 501),
        (EngineDeviceNotReadyError("down"), 409),
        (EngineStageNotLiveError("not live"), 409),
        (DeviceAdapterTimeoutError("timeout"), 504),
    ],
)
def test_failures_remain_failures(client, relay, error, status):
    relay.raises = error
    fails(client.get(f"/openapi/v1/bots/{BOT}/engine/default-model"), status)


def test_foreign_bot_is_masked_before_transport(client, relay):
    fails(client.get("/openapi/v1/bots/other/engine/default-model"), 404)
    assert relay.attempts == []


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_published_stage_is_forwarded_without_draft_fallback(client, relay, stage):
    relay.set_bot_type("service")
    relay.results = [EngineResult(data={"id": "published/model"})]
    assert ok(
        client.get(f"/openapi/v1/bots/{BOT}/engine/default-model?stage={stage}")
    ) == {"model_id": "published/model"}
    assert relay.calls[0]["stage"] == stage
