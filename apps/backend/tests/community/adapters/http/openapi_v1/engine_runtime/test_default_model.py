"""Default-model forwarding and public boundary tests."""
import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine.default_model import router
from agentclaw.community.core.engine_runtime.errors import EngineCapabilityUnsupportedError
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok


@pytest.fixture
def client(make_client):
    return make_client(router)


def path(bot=BOT):
    return f"/openapi/v1/bots/{bot}/engine/default-model"


@pytest.mark.parametrize("model", ["p/m", None])
def test_forwards_read_and_publishes_only_identifier(client, relay, model):
    relay.results = [EngineResult(data={"model_id": model, "config": {"secret": "private"}})]
    assert ok(client.get(path())) == {"model_id": model}
    assert relay.calls[0]["path"] == "/api/engine/default-model"
    assert relay.calls[0]["method"] == "GET"
    assert relay.calls[0]["stage"] == "draft"
    assert relay.calls[0]["bot_id"] == BOT
    assert relay.calls[0]["owner_id"] == OWNER


@pytest.mark.parametrize("payload", [{}, [], "p/m", {"model_id": 7}, {"model_id": " "}])
def test_bad_upstream_does_not_become_null(client, relay, payload):
    relay.results = [EngineResult(data=payload)]
    fails(client.get(path()), 502)


def test_unknown_bot_never_forwards(client, relay):
    fails(client.get(path("other")), 404)
    assert relay.attempts == []


def test_non_operator_never_forwards(make_client, relay):
    client = make_client(router, caller="stranger")
    fails(client.get(path()+f"?owner_id={OWNER}"), 404)
    assert relay.attempts == []


def test_member_can_read(make_client, relay):
    relay.add_operator("collaborator")
    relay.results = [EngineResult(data={"model_id": "p/m"})]
    client = make_client(router, caller="collaborator")
    assert ok(client.get(path()+f"?owner_id={OWNER}")) == {"model_id": "p/m"}


def test_unsupported_is_not_unconfigured(client, relay):
    relay.raises = EngineCapabilityUnsupportedError("unsupported")
    fails(client.get(path()), 501)
