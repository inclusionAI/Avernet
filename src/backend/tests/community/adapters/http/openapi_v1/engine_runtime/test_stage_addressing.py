"""Stage addressing on the engine-runtime surface, behaviour and document.

The behavioural half: the default is the draft byte-for-byte, a named stage
travels to the forward unchanged, a stage a bot cannot have is the one fixed
409, and a value outside the enum never reaches a handler. The document half,
in the shape of ``test_explicit_user_id.py``: ``stage`` is an optional query
parameter on exactly the engine-runtime operations (current and retiring) plus
the five per-bot file operations that address a runtime, and ``owner_id`` on the
engine-runtime ones plus the bot-scoped authorization and skills operations — asserted against the
generated description so a later operation is covered without editing this
file.
"""

from __future__ import annotations

from functools import lru_cache

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from tests.community.adapters.http.openapi_v1.conftest import public_document
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.converter_human_chat_policy import (
    BASE as HUMAN_CHAT_BASE,
    OPERATIONS as HUMAN_CHAT_OPERATIONS,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    router as sessions_router,
)
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok


#: The engine-runtime paths, taken from the routers themselves rather than
#: guessed from a segment. Segment-matching was wrong the moment engine config
#: moved to ``/{bot_id}/engine/config``: it sits under the ``engine`` literal by
#: address but is not an engine-runtime operation, takes neither ``owner_id``
#: nor ``stage``, and documents no 501 — so a name test would sweep it in and
#: assert it carries parameters it has no business carrying.
def _engine_runtime_paths() -> frozenset[str]:
    from agentclaw.community.adapters.http.openapi_v1 import _ENGINE_RUNTIME_GROUPS
    from agentclaw.community.adapters.http.openapi_v1.deprecated import (
        ENGINE_RUNTIME_GROUPS as LEGACY_ENGINE_RUNTIME,
    )

    # The retiring addresses are mounted with the same response table and take
    # the same parameters — they are the same operations at a former address —
    # so they belong in this set until they are deleted. Leaving them out would
    # have the test assert they carry neither owner_id nor stage, which is the
    # opposite of the parity this feature promises.
    return frozenset(
        route.path.replace(":path", "")
        for group in [*_ENGINE_RUNTIME_GROUPS, *LEGACY_ENGINE_RUNTIME]
        for route in group.routes
        if isinstance(route, APIRoute)
    )


def _is_engine_runtime(path: str) -> bool:
    """Whether *path* is served by one of the engine-runtime groups."""
    return path in _engine_runtime_paths()


#: The per-bot file operations. They read or write a file **on** the addressed
#: runtime, so they name one the same way the forwarding groups do — but they
#: are not engine-runtime operations: they carry ``user_id`` rather than
#: ``owner_id``, and engine-config publishes the ordinary error table because it
#: cannot produce the 501 or 504 those groups document.
#:
#: The retiring twins of these five are deliberately absent. Their contract is
#: frozen, so they must not have grown the parameter — which the exclusivity
#: assertion below is what proves.
_STAGE_ADDRESSED_ELSEWHERE = {
    ("post", "/openapi/v1/bots/{bot_id}/iam-token"),
    ("get", "/openapi/v1/bots/{bot_id}/caller-context"),
    ("get", "/openapi/v1/bots/{bot_id}/engine/config"),
    ("put", "/openapi/v1/bots/{bot_id}/engine/config"),
    ("get", "/openapi/v1/bots/{bot_id}/identity"),
    ("get", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
    ("put", "/openapi/v1/bots/{bot_id}/identity/{file_type}"),
}

#: The other operations that address a bot by ``(owner, bot_id)``.
#:
#: They take ``owner_id`` for the same reason and with the same default — the
#: caller's own bot — because ``bot_id`` alone does not identify one. They do
#: **not** take ``stage``: there is no runtime in question when you are
#: recording who may reach a bot, or listing a bot's stored skills.
_OWNER_ADDRESSED_ELSEWHERE = {
    ("get", "/openapi/v1/bots/{bot_id}/caller-context"),
    ("patch", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/call-type"),
    ("patch", "/openapi/v1/bots/{bot_id}/clis/{cli_code}/call-type"),
    ("get", "/openapi/v1/bots/{bot_id}/authorized-apps"),
    ("post", "/openapi/v1/bots/{bot_id}/authorized-apps"),
    ("delete", "/openapi/v1/bots/{bot_id}/authorized-apps/{app_id}"),
    # The skills group addresses an owner too, and now says so under the same
    # name as everywhere else — it spelled this owner_entity_id until the
    # bot-first change, which is why it was not in this set before.
    ("get", "/openapi/v1/bots/{bot_id}/skills"),
    ("post", "/openapi/v1/bots/{bot_id}/skills"),
    ("post", "/openapi/v1/bots/{bot_id}/skills/upload-folder"),
    (
        "post",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    ),
    (
        "get",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references",
    ),
    (
        "get",
        "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skill-center-references/{reference_id}",
    ),
    # The config manifest is collaborator-scoped — MEMBER to read, ADMIN to
    # write — so it addresses a bot that may be someone else's, and takes the
    # owner half of ``(owner, bot_id)``. No ``stage``: a manifest is one row per
    # bot, not one per runtime, so there is nothing for the parameter to name.
    ("get", "/openapi/v1/bots/{bot_id}/config-manifest"),
    ("put", "/openapi/v1/bots/{bot_id}/config-manifest"),
    ("delete", "/openapi/v1/bots/{bot_id}/config-manifest"),
    ("get", "/openapi/v1/bots/{bot_id}/config-manifest/capabilities"),
    # Apply and its two reads address the same (owner, bot_id) pair as the
    # document they act on, and take no ``stage`` for the same reason: an
    # apply targets the bot, not one of its runtimes.
    ("post", "/openapi/v1/bots/{bot_id}/config-manifest/apply"),
    ("get", "/openapi/v1/bots/{bot_id}/config-manifest/last-apply"),
    ("get", "/openapi/v1/bots/{bot_id}/config-manifest/applies/{apply_id}"),
    # The product chat reads address a bot that may be shared with the acting
    # user, so they take the owner half of ``(owner, bot_id)`` for the same
    # reason and with the same default — the caller's own bot.
    ("get", "/openapi/v1/bots/{bot_id}/chats"),
    ("get", "/openapi/v1/bots/{bot_id}/chats/{trace_id}"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/upgrade"),
    (
        "post",
        "/openapi/v1/bots/{bot_id}/lifecycle/{publication_id}/upgrade",
    ),
    ("get", "/openapi/v1/bots/{bot_id}/lifecycle"),
    ("delete", "/openapi/v1/bots/{bot_id}/lifecycle"),
    ("get", "/openapi/v1/bots/{bot_id}/lifecycle/approval"),
    ("put", "/openapi/v1/bots/{bot_id}/lifecycle/approval"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/advance"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/restart"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/offline"),
    ("post", "/openapi/v1/bots/{bot_id}/lifecycle/retry"),
    ("get", "/openapi/v1/bots/{bot_id}/edit-lock"),
    ("post", "/openapi/v1/bots/{bot_id}/edit-lock"),
    ("delete", "/openapi/v1/bots/{bot_id}/edit-lock"),
    ("post", "/openapi/v1/bots/{bot_id}/edit-lock/steal"),
    ("get", "/openapi/v1/bots/{bot_id}/containers"),
    ("post", "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart"),
    ("get", "/openapi/v1/bots/{bot_id}/diagnostics/health"),
    ("post", "/openapi/v1/bots/{bot_id}/diagnostics/health-check"),
    ("get", "/openapi/v1/bots/{bot_id}/channels"),
    ("post", "/openapi/v1/bots/{bot_id}/channels"),
    ("get", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
    ("patch", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/channels/{channel_id}"),
    ("put", "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status"),
    ("get", "/openapi/v1/bots/{bot_id}/editors"),
    ("post", "/openapi/v1/bots/{bot_id}/editors"),
    ("post", "/openapi/v1/bots/{bot_id}/editor-requests"),
    ("patch", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/editors/{editor_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/editors/me"),
    ("get", "/openapi/v1/bots/{bot_id}/render-screens"),
    ("post", "/openapi/v1/bots/{bot_id}/render-screens"),
    (
        "patch",
        "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    ),
    (
        "delete",
        "/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    ),
    # Phase-1 Skill controls are all owner-addressed. The Set and MCP routes
    # may operate on a collaborator's Bot, so their caller and target owner
    # cannot be collapsed into one field.
    ("get", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/skills/{skill_id}"),
    ("post", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/activate"),
    ("post", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/deactivate"),
    ("get", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/content"),
    ("get", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"),
    ("put", "/openapi/v1/bots/{bot_id}/skills/{skill_id}/parameters"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets"),
    ("post", "/openapi/v1/bots/{bot_id}/skill-sets"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets/resources"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"),
    ("put", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}"),
    ("post", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/activate"),
    ("post", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/deactivate"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills"),
    ("put", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"),
    ("delete", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/skills/{skill_id}"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps"),
    ("put", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"),
    ("delete", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcps/{server_code}"),
    ("get", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permissions"),
    ("post", "/openapi/v1/bots/{bot_id}/skill-sets/{set_id}/mcp-permission-requests"),
    ("get", "/openapi/v1/bots/{bot_id}/mcps"),
    ("post", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/activate"),
    ("post", "/openapi/v1/bots/{bot_id}/mcps/{server_code}/deactivate"),
}
_OWNER_ADDRESSED_ELSEWHERE |= {
    (method.lower(), HUMAN_CHAT_BASE + suffix)
    for method, suffix in HUMAN_CHAT_OPERATIONS
}


@pytest.fixture
def client(make_client):
    return make_client(sessions_router)


def _base(bot: str = BOT) -> str:
    return f"/openapi/v1/bots/{bot}/sessions"


# ── behaviour ────────────────────────────────────────────────────────────────


def test_the_default_is_the_draft_byte_for_byte(client, relay):
    """A request naming no stage forwards ``stage == "draft"`` — the exact
    behaviour of the surface before stages were addressable."""
    relay.set_bot_type("service")
    ok(client.get(_base()))
    assert [c["stage"] for c in relay.calls] == ["draft"]


#: One representative route per relay-forwarding group — a group whose router
#: gated on the requested stage but forwarded a pasted literal would slip a
#: sessions-only pin.
_GROUP_ROUTES = [
    ("sessions", f"/openapi/v1/bots/{BOT}/sessions"),
    ("engine", f"/openapi/v1/bots/{BOT}/engine/capabilities"),
    ("models", f"/openapi/v1/bots/{BOT}/models"),
    ("nodes", f"/openapi/v1/bots/{BOT}/nodes"),
    ("approvals", f"/openapi/v1/bots/{BOT}/approvals/mode?session_key=k"),
]


@pytest.mark.parametrize(("group", "url"), _GROUP_ROUTES, ids=lambda v: str(v))
@pytest.mark.parametrize("stage", ["verify", "online"])
def test_a_named_stage_travels_to_the_forward_unchanged(
    make_client, relay, group, url, stage
):
    """The stage the gate admitted is the stage the forward addresses, in
    every relay-forwarding group — the relay's required parameter makes an
    *omitted* stage a TypeError, and this pins the value itself."""
    import importlib

    module = importlib.import_module(
        "agentclaw.community.adapters.http.openapi_v1.engine_runtime." + group
    )
    relay.set_bot_type("service")
    relay.results = [
        EngineResult(
            data=[]
            if group == "nodes"
            else {"supported": [], "sessionKey": "k", "mode": "approve", "id": "s"}
        )
    ]
    client = make_client(module.router)
    resp = client.get(url, params={"stage": stage})
    assert resp.status_code == 200, resp.json()
    assert [c["stage"] for c in relay.calls] == [stage]


def test_the_connection_build_receives_the_named_stage(relay):
    """The socket side of the same pin: the router passes the request's stage
    to ``build`` verbatim."""
    from fastapi_injector import attach_injector
    from injector import Injector, Module

    from tests.community.adapters.http.openapi_v1.conftest import (
        user_scoped_client,
    )
    from tests.community.adapters.http.openapi_v1.engine_runtime import (
        test_operator_access as op,
    )
    from agentclaw.community.adapters.http.openapi_v1.dependencies import (
        require_principal,
    )
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.connection import (
        router as connection_router,
    )
    from agentclaw.community.api.engine_connection_service import (
        EngineConnectionServiceProtocol,
    )

    relay.set_bot_type("service")
    connections = op._Connections(relay)

    class _M(Module):
        def configure(self, binder):
            binder.bind(EngineConnectionServiceProtocol, to=connections)

    app = FastAPI()
    app.include_router(connection_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": OWNER}
    attach_injector(app, Injector([_M()]))
    client = user_scoped_client(app, OWNER)

    resp = client.get(f"/openapi/v1/bots/{BOT}/connection", params={"stage": "online"})
    assert resp.status_code == 200, resp.json()
    assert [b["stage"] for b in connections.builds] == ["online"]


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_a_published_stage_on_a_personal_bot_is_409(client, relay, stage):
    """A personal bot has only its workspace; naming a published stage for
    one answers as a stage with no live runtime — before any forward."""
    body = fails(client.get(_base(), params={"stage": stage}), 409)
    assert body["message"] == "No live runtime at the requested stage"
    assert relay.calls == []


def test_a_stage_outside_the_enum_never_reaches_a_handler(client, relay):
    """``eval`` exists in the publish flow but has no long-lived runtime; it
    is not in the published enum, so validation answers before any code of
    this surface runs."""
    resp = client.get(_base(), params={"stage": "eval"})
    assert resp.status_code == 422
    assert relay.attempts == []


def test_a_dead_stage_is_the_fixed_409(client, relay):
    """When the relay reports the stage not live (nothing validating, nothing
    released), the caller sees one fixed answer, distinguishable from the
    masked 404 and from device-not-ready."""
    from agentclaw.community.core.engine_runtime.errors import (
        EngineStageNotLiveError,
    )

    relay.set_bot_type("service")
    relay.raises = EngineStageNotLiveError("no live verify runtime")
    body = fails(client.get(_base(), params={"stage": "verify"}), 409)
    assert body["message"] == "No live runtime at the requested stage"


# ── the document ─────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _schema() -> dict:
    # Cached: four document tests read the same generated description, and
    # assembling the whole public surface per test quadruples the cost.
    return public_document()


def _operations(schema: dict):
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                yield path, method, operation


def _query_params(operation: dict) -> dict[str, dict]:
    return {
        p["name"]: p for p in operation.get("parameters", []) if p.get("in") == "query"
    }


def test_owner_id_and_stage_are_on_exactly_the_engine_runtime_operations():
    schema = _schema()
    engine_runtime, carrying_stage, carrying_owner = [], [], []
    for path, method, operation in _operations(schema):
        params = _query_params(operation)
        if _is_engine_runtime(path):
            engine_runtime.append((method, path))
            assert "owner_id" in params, f"{method.upper()} {path} lacks owner_id"
            assert "stage" in params, f"{method.upper()} {path} lacks stage"
        if "stage" in params:
            carrying_stage.append((method, path))
            assert not params["stage"].get("required", False), path
        if "owner_id" in params:
            carrying_owner.append((method, path))
            # Never required anywhere: omitting it means the caller's own bot,
            # which is what every one of these operations meant before the
            # parameter existed.
            assert not params["owner_id"].get("required", False), path

    # The original 16 operations also answer at their former addresses while
    # callers migrate. The three newly added favorite operations have only
    # their bot-first address; engine restart and nodes also have no legacy
    # alias. Session File is OpenAPI-only, yielding 27 current + 16 retiring
    # operations.
    assert len(engine_runtime) == 43
    assert sorted(carrying_stage) == sorted(
        set(engine_runtime) | _STAGE_ADDRESSED_ELSEWHERE
    ), (
        "stage belongs to the engine-runtime operations, Caller preparation, "
        "and the five per-bot file operations, and to nothing else by accident — in particular to "
        "no retiring address of those five, whose contract is frozen"
    )
    assert sorted(carrying_owner) == sorted(
        set(engine_runtime) | _OWNER_ADDRESSED_ELSEWHERE
    ), (
        "owner_id belongs to engine-runtime and the explicitly listed "
        "owner-addressed operations, and to nothing else by accident"
    )


def test_the_stage_enum_publishes_exactly_the_three_runtimes():
    schema = _schema()
    enum = schema["components"]["schemas"]["RuntimeStage"]["enum"]
    assert sorted(enum) == ["draft", "online", "verify"]
    assert [m.value for m in RuntimeStage] == ["draft", "verify", "online"]


def test_neither_parameter_is_ever_a_body_field_or_a_path_segment():
    """Scoped to the engine-runtime operations' request bodies and addresses — a
    future *response* model elsewhere on the surface may legitimately expose
    an ``owner_id`` or ``stage`` field; the placement rule is about where
    these two request parameters travel."""
    schema = _schema()
    components = schema.get("components", {}).get("schemas", {})
    for path, method, operation in _operations(schema):
        if not _is_engine_runtime(path):
            continue
        body = operation.get("requestBody", {})
        ref = (
            body.get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("$ref", "")
        )
        if ref:
            model = components.get(ref.rsplit("/", 1)[-1], {})
            for field in model.get("properties", {}) or {}:
                assert field not in ("owner_id", "stage"), (
                    f"{field} is a body field on {method.upper()} {path}"
                )
        assert "{owner_id}" not in path and "{stage}" not in path


def test_the_409_is_documented_on_every_engine_runtime_operation():
    """A regression pin, not an exclusivity claim: 409 is documented
    surface-wide (``ERROR_RESPONSES``), which is what covers the stage
    refusal — this fails only if the engine-runtime groups stop carrying
    it."""
    schema = _schema()
    for path, method, operation in _operations(schema):
        if _is_engine_runtime(path):
            assert "409" in operation.get("responses", {}), (
                f"{method.upper()} {path} does not document 409"
            )
