"""Opt-in filters share the operator gate; old page-number requests stay intact."""

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.router import router
from agentclaw.community.api.session_source_listing import SessionSourceListingProtocol
from agentclaw.community.core.engine_runtime.models import EngineResult
from agentclaw.community.core.engine_runtime.session_listing import SessionSourceListingService
from agentclaw.community.di.modules.engine_runtime_module import EngineRuntimeModule
from injector import Injector, Module

from .conftest import BOT, OWNER, fails, ok

BASE = f"/openapi/v1/bots/{BOT}/sessions"


def test_stable_session_page_serialization_schema_keeps_existing_fields():
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import SessionPage
    schema = SessionPage.model_json_schema(mode="serialization")
    assert schema.get("description")
    assert {"total", "items", "source", "has_more", "next_cursor", "scan_limited"} <= schema["properties"].keys()
    assert set(schema["required"]) == {"total", "items"}


def row(user):
    return {"id": f"agent:main:session:s-{user}:user:{user}", "title": user}


@pytest.mark.parametrize("caller", [OWNER, "collaborator"])
@pytest.mark.parametrize("source", ["mine", "others"])
@pytest.mark.parametrize("engine", ["openclaw", "aicoding", "claude_code", "teclaw"])
def test_owner_and_collaborator_use_authenticated_caller(make_client, relay, caller, source, engine):
    relay.add_operator("collaborator")
    relay.set_bot_type("service")
    relay.set_active_engine(engine)
    native = source == "mine" and engine != "teclaw"
    # The boundary returns the native-filtered page only when source is sent.
    relay.results = [EngineResult(data=[row(caller)] if native else [row(OWNER), row("collaborator"), row("third")])]
    response = make_client(router, caller=caller).get(BASE, params={
        "owner_id": OWNER, "source": source, "stage": "online", "page_size": 10,
        "agent_id": "main", "device_uuid": "device-1",
    })
    data = ok(response)
    expected = [user for user in [OWNER, "collaborator", "third"] if (user == caller) == (source == "mine")]
    assert [item["title"] for item in data["items"]] == expected
    assert data["has_more"] is False and data["next_cursor"] is None
    assert data["source"] == source and data["total"] == len(expected)
    expected_params = {"offset": 0, "limit": 11, "agent_id": "main"}
    if native:
        expected_params.update(user_id=caller, source="all_but_others")
    assert relay.calls[0]["params"] == expected_params
    assert relay.calls[0]["stage"] == "online"
    assert relay.calls[0]["device_uuid"] == "device-1"


@pytest.mark.parametrize("source", ["mine", "others"])
def test_foreign_bot_never_scanned(make_client, relay, source):
    fails(make_client(router, caller="stranger").get(BASE, params={"owner_id": OWNER, "source": source}), 404)
    assert relay.calls == []


@pytest.mark.parametrize("query", [
    {"source": "invalid"}, {"source": "others", "page": 2},
    {"source": "others", "f_user_id": "friend"}, {"source_cursor": "invalid"},
    {"source": "others", "source_cursor": "!"},
])
def test_bad_selector_combinations_refused_without_device(make_client, relay, query):
    response = make_client(router).get(BASE, params=query)
    assert response.status_code == 422
    assert relay.calls == []


def test_legacy_response_shape_and_pagination_unchanged(make_client, relay):
    relay.results = [EngineResult(data=[row(OWNER)])]
    result = ok(make_client(router).get(BASE, params={"page": 2, "page_size": 3}))
    assert set(result) == {"total", "items"}
    assert relay.calls[0]["params"] == {
        "offset": 3, "limit": 4, "user_id": OWNER, "source": "all_but_others",
    }


def test_explicit_source_does_not_expand_friend_permissions(make_client, relay, expert, friendships):
    response = make_client(router).get(BASE, params={"source": "others", "f_user_id": "friend"})
    assert response.status_code == 422
    assert expert.calls == [] and friendships.calls == [] and relay.calls == []


def test_source_cursor_continuation_is_reauthorized(make_client, relay):
    relay.add_operator("collaborator")
    relay.results = [EngineResult(data=[row("third"), row("fourth")])]
    client = make_client(router, caller="collaborator")
    cursor = ok(client.get(BASE, params={"owner_id": OWNER, "source": "others", "page_size": 1}))["next_cursor"]
    assert cursor
    relay.operators.clear()
    attempts = len(relay.calls)
    fails(client.get(BASE, params={"owner_id": OWNER, "source": "others", "page_size": 1, "source_cursor": cursor}), 404)
    assert len(relay.calls) == attempts


def test_production_module_wires_source_service_to_same_relay(relay):
    from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol

    class BindRelay(Module):
        def configure(self, binder):
            binder.bind(EngineRuntimeRelayProtocol, to=relay)

    service = Injector([EngineRuntimeModule(), BindRelay()]).get(SessionSourceListingProtocol)
    assert isinstance(service, SessionSourceListingService)
    assert service._relay is relay
