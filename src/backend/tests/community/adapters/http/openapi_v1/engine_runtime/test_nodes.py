"""Endpoint tests for the read-only nodes runtime group."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.nodes import (
    router as nodes_router,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
)
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok


@pytest.fixture
def client(make_client):
    return make_client(nodes_router)


NODE = {
    "nodeId": "node-01",
    "displayName": "Joseph's Mac",
    "platform": "darwin",
    "version": "1.2.0",
    "capabilities": ["screen", "shell"],
    "commands": ["system.run"],
    "remoteIp": "203.0.113.10",
    "status": "online",
    "metadata": {"provider": "internal"},
    "raw": {"secret": "not-public"},
}


def test_list_nodes_maps_the_frontend_contract_and_drops_internal_fields(client, relay):
    relay.results = [EngineResult(data=[NODE])]

    data = ok(client.get(f"/openapi/v1/bots/{BOT}/nodes"))

    assert data == [
        {
            "node_id": "node-01",
            "display_name": "Joseph's Mac",
            "platform": "darwin",
            "version": "1.2.0",
            "capabilities": ["screen", "shell"],
            "commands": ["system.run"],
            "remote_ip": "203.0.113.10",
            "status": "online",
        }
    ]
    assert "metadata" not in str(data)
    assert "not-public" not in str(data)


def test_list_nodes_forwards_filters_and_pagination(client, relay):
    relay.results = [EngineResult(data=[])]

    ok(
        client.get(
            f"/openapi/v1/bots/{BOT}/nodes",
            params={
                "status": "online",
                "platform": "darwin",
                "limit": 7,
                "offset": 14,
            },
        )
    )

    assert relay.calls[0]["path"] == "/api/nodes"
    assert relay.calls[0]["params"] == {
        "status": "online",
        "platform": "darwin",
        "limit": 7,
        "offset": 14,
    }


def test_list_nodes_forwards_the_engine_defaults(client, relay):
    relay.results = [EngineResult(data=[])]

    ok(client.get(f"/openapi/v1/bots/{BOT}/nodes"))

    assert relay.calls[0]["params"] == {"limit": 20, "offset": 0}


def test_list_nodes_uses_the_named_service_runtime(client, relay):
    relay.set_bot_type("service")
    relay.results = [EngineResult(data=[])]

    ok(client.get(f"/openapi/v1/bots/{BOT}/nodes", params={"stage": "online"}))

    assert relay.calls[0]["stage"] == "online"


def test_foreign_bot_is_masked_before_node_inventory_is_fetched(client, relay):
    assert fails(client.get("/openapi/v1/bots/other/nodes"), 404)
    assert relay.calls == []
    assert relay.attempts == []


def test_collaborator_can_list_the_owners_nodes(make_client, relay):
    relay.add_operator("u2")
    relay.results = [EngineResult(data=[])]
    collaborator = make_client(nodes_router, caller="u2")

    ok(collaborator.get(f"/openapi/v1/bots/{BOT}/nodes", params={"owner_id": OWNER}))

    assert relay.calls[0]["owner_id"] == OWNER


@pytest.mark.parametrize("payload", [{}, None, ["not-an-object"], [{}]])
def test_malformed_node_payload_is_an_upstream_error(client, relay, payload):
    relay.results = [EngineResult(data=payload)]

    body = fails(client.get(f"/openapi/v1/bots/{BOT}/nodes"), 502)

    assert body["message"] == "Engine service error"


def test_capability_unsupported_is_501(client, relay):
    relay.raises = EngineCapabilityUnsupportedError("node.list unsupported")

    fails(client.get(f"/openapi/v1/bots/{BOT}/nodes"), 501)


def test_node_query_bounds_are_validated_before_forwarding(client, relay):
    response = client.get(f"/openapi/v1/bots/{BOT}/nodes", params={"limit": 101})

    assert response.status_code == 422
    assert relay.attempts == []
