"""Endpoint tests for the engine (read-only) and models groups (Task 8)."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.engine import (
    router as engine_router,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.models import (
    router as models_router,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
)
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok


@pytest.fixture
def engine_client(make_client):
    return make_client(engine_router)


@pytest.fixture
def models_client(make_client):
    return make_client(models_router)


# ── engine/status: the one raw-payload route ─────────────────────────────────

RAW_STATUS = {
    "engine": "openclaw",
    "active_connections": 2,
    "process": {"running": True, "pid": 4711},
    "transition": {"phase": "completed"},
}


def test_status_is_requested_unenveloped(engine_client, relay):
    """``/api/engine/status`` returns EngineManager.status() with no envelope.

    Requesting it as enveloped would fail every call against a healthy device,
    since the body has no ``success`` key.
    """
    relay.results = [EngineResult(data=RAW_STATUS)]
    ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/status"))
    assert relay.calls[0]["enveloped"] is False
    assert relay.calls[0]["path"] == "/api/engine/status"


def test_status_publishes_only_stable_fields(engine_client, relay):
    """``process`` and ``transition`` are open dicts assembled ad hoc."""
    relay.results = [EngineResult(data=RAW_STATUS)]
    data = ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/status"))
    assert data == {"engine": "openclaw", "active_connections": 2, "running": True}
    assert "4711" not in str(data)


def test_status_of_a_down_engine_is_not_an_error(engine_client, relay):
    relay.results = [EngineResult(data={**RAW_STATUS, "process": {"running": False}})]
    assert (
        ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/status"))["running"]
        is False
    )


# ── engine/capabilities ──────────────────────────────────────────────────────


def test_capabilities_publishes_names_never_the_engines_prose(engine_client, relay):
    """``limited``/``fallback`` values are internal text, some not English."""
    relay.results = [
        EngineResult(
            data={
                "supported": ["session.list", "model.list"],
                "limited": {
                    "session.create": (
                        "teamclaw-aicoding-relay has no explicit sessions.create; "
                        "OCB pre-allocates the sessionKey"
                    )
                },
                "fallback": {"mcp.start": "通过 mcporter 命令启动"},
            }
        )
    ]
    data = ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/capabilities"))
    assert data["supported"] == ["model.list", "session.list"]
    assert data["limited"] == ["session.create"]
    assert data["unavailable"] == ["mcp.start"]
    body = str(data)
    assert "teamclaw-aicoding-relay" not in body
    assert "mcporter" not in body
    assert body.isascii()


def test_capabilities_tolerates_a_missing_section(engine_client, relay):
    relay.results = [EngineResult(data={"supported": ["session.list"]})]
    data = ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/capabilities"))
    assert data["limited"] == [] and data["unavailable"] == []


# ── engine/available ─────────────────────────────────────────────────────────


def test_available_engines(engine_client, relay):
    relay.results = [
        EngineResult(
            data={
                "engines": [
                    {"name": "openclaw", "version": "1.0.0", "active": True},
                    {"name": "claude_code", "version": "1.0.0", "active": False},
                ]
            }
        )
    ]
    data = ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/available"))
    assert [e["engine"] for e in data] == ["openclaw", "claude_code"]
    assert [e["active"] for e in data] == [True, False]
    assert relay.calls[0]["path"] == "/api/engine/list"


def test_switch_is_not_exposed(engine_client):
    """``switch`` stays unexposed because engine choice is fixed at creation.

    ``restart`` is exposed separately by the engine runtime adapter.
    """
    resp = engine_client.post(f"/openapi/v1/bots/{BOT}/engine/switch")
    assert resp.status_code in (404, 405), resp.status_code


# ── models ───────────────────────────────────────────────────────────────────


def test_list_models(models_client, relay):
    """The engine wraps this one: ``{"models": [...], "total": n}``.

    An earlier version of this test fed a bare list — a shape the engine never
    returns — which let a handler that read ``result.data`` as a list ship while
    returning an empty page against every real device.
    """
    relay.results = [
        EngineResult(
            data={
                "models": [{"id": "openai/gpt-5.3", "name": "G", "provider": "openai"}],
                "total": 1,
            }
        )
    ]
    data = ok(models_client.get(f"/openapi/v1/bots/{BOT}/models"))
    assert data["total"] == 1
    assert data["items"][0]["model_id"] == "openai/gpt-5.3"


def test_owner_can_list_models_for_an_explicit_bcn_friend(
    models_client, relay, friendships
):
    friendships.allowed = True
    relay.results = [EngineResult(data={"models": [{"id": "model-1"}], "total": 1})]

    data = ok(
        models_client.get(
            f"/openapi/v1/bots/{BOT}/models", params={"f_user_id": "friend-1"}
        )
    )

    assert data["total"] == 1
    assert friendships.calls[0]["human_id"] == "friend-1"


def test_list_models_prefers_the_engines_total(models_client, relay):
    relay.results = [
        EngineResult(data={"models": [{"id": "a"}, {"id": "b"}], "total": 97})
    ]
    assert ok(models_client.get(f"/openapi/v1/bots/{BOT}/models"))["total"] == 97


def test_a_bare_list_payload_yields_an_empty_page_not_a_crash(models_client, relay):
    relay.results = [EngineResult(data=[{"id": "x"}])]
    assert ok(models_client.get(f"/openapi/v1/bots/{BOT}/models"))["total"] == 0


@pytest.mark.parametrize(
    "bad", ["../../api/bash/exec", "..%2f..%2fapi%2fnodes", "a/../../b", "../nodes"]
)
def test_dot_segments_in_a_model_id_are_rejected(models_client, relay, bad):
    """The id spans slashes and is concatenated into the engine path.

    httpx normalises dot segments when building the request, so ".." would
    reach engine routes this surface deliberately does not wrap — on the
    caller's own bot, but outside the published scope all the same.
    """
    resp = models_client.get(f"/openapi/v1/bots/{BOT}/models/{bad}")
    assert resp.status_code in (404, 400), resp.status_code
    assert relay.calls == []


def test_get_model_id_with_a_slash_survives_routing(models_client, relay):
    """Provider-qualified ids contain a slash; the :path converter handles it."""
    relay.results = [
        EngineResult(data={"id": "openai/gpt-5.3", "name": "G", "provider": "openai"})
    ]
    ok(models_client.get(f"/openapi/v1/bots/{BOT}/models/openai/gpt-5.3"))
    assert relay.calls[0]["path"] == "/api/models/openai/gpt-5.3"


def test_unknown_model_is_404(models_client, relay):
    relay.results = [EngineResult(data=None)]
    assert (
        fails(models_client.get(f"/openapi/v1/bots/{BOT}/models/nope"), 404)["message"]
        == "Not found"
    )


# ── shared behaviour ─────────────────────────────────────────────────────────


def test_both_groups_serve_service_bots(engine_client, models_client, relay):
    """Engine state and models are device facts, served for both bot types."""
    relay.set_bot_type("service")
    relay.results = [EngineResult(data=RAW_STATUS)]
    ok(engine_client.get(f"/openapi/v1/bots/{BOT}/engine/status"))
    relay.results = [EngineResult(data=[])]
    ok(models_client.get(f"/openapi/v1/bots/{BOT}/models"))


def test_foreign_bot_is_masked_404_without_a_device_call(engine_client, relay):
    assert fails(engine_client.get("/openapi/v1/bots/other/engine/status"), 404)
    assert relay.calls == []


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (EngineDeviceNotReadyError("cold"), 409),
        (EngineCapabilityUnsupportedError("nope"), 501),
    ],
)
def test_relay_errors_are_enveloped(models_client, relay, exc, status):
    relay.raises = exc
    fails(models_client.get(f"/openapi/v1/bots/{BOT}/models"), status)


# ── the operator gate ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("client_name", "path"),
    [
        ("engine_client", f"/openapi/v1/bots/{BOT}/engine/status"),
        ("engine_client", f"/openapi/v1/bots/{BOT}/engine/capabilities"),
        ("engine_client", f"/openapi/v1/bots/{BOT}/engine/available"),
        ("models_client", f"/openapi/v1/bots/{BOT}/models"),
        ("models_client", f"/openapi/v1/bots/{BOT}/models/openai/gpt-5.3"),
    ],
)
def test_a_service_bot_is_served_at_its_draft_device(request, relay, client_name, path):
    """Every route serves a service bot at its DRAFT binding by default.

    A request that names no stage behaves exactly as before stages were
    addressable: every forward carries ``stage == "draft"``, never the
    published runtime the relay's internal default would resolve.
    """
    client = request.getfixturevalue(client_name)
    relay.set_bot_type("service")
    relay.results = [EngineResult(data={"engines": [], "models": []})]
    resp = client.get(path)
    assert resp.status_code == 200, resp.json()
    assert relay.calls, "the route never forwarded"
    assert all(c["stage"] == "draft" for c in relay.calls)


def test_a_collaborator_is_served_and_a_stranger_is_the_masked_404(make_client, relay):
    """The flip of the old shared-bot 501: operators are adjudicated per
    caller — a collaborator reads engine state, a stranger's answer is
    byte-identical to a bot that does not exist."""
    relay.add_operator("u2")
    relay.results = [EngineResult(data=RAW_STATUS)]
    collaborator = make_client(engine_router, caller="u2")
    ok(
        collaborator.get(
            f"/openapi/v1/bots/{BOT}/engine/status", params={"owner_id": OWNER}
        )
    )
    stranger = make_client(engine_router, caller="u9")
    refused = fails(
        stranger.get(
            f"/openapi/v1/bots/{BOT}/engine/status", params={"owner_id": OWNER}
        ),
        404,
    )
    assert refused["message"] == "Not found"


def test_an_unknown_bot_type_gets_501_without_touching_the_device(models_client, relay):
    relay.set_bot_type("something-new")
    body = fails(models_client.get(f"/openapi/v1/bots/{BOT}/models"), 501)
    assert body["message"] == "Not supported for this bot type"
    assert relay.calls == []
