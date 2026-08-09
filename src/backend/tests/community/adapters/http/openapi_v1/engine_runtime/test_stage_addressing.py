"""Stage addressing on the engine-runtime surface, behaviour and document.

The behavioural half: the default is the draft byte-for-byte, a named stage
travels to the forward unchanged, a stage a bot cannot have is the one fixed
409, and a value outside the enum never reaches a handler. The document half,
in the shape of ``test_explicit_user_id.py``: ``owner_id`` and ``stage`` are
optional query parameters on exactly the sixteen engine-runtime operations,
and nowhere else — asserted against the generated description so a later
operation is covered without editing this file.
"""

from __future__ import annotations

from functools import lru_cache

import pytest
from fastapi import FastAPI

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    router as sessions_router,
)
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok

_ENGINE_RUNTIME_PREFIXES = (
    "/openapi/v1/bots/sessions",
    "/openapi/v1/bots/engine",
    "/openapi/v1/bots/models",
    "/openapi/v1/bots/approvals",
    "/openapi/v1/bots/connection",
)


@pytest.fixture
def client(make_client):
    return make_client(sessions_router)


def _base(bot: str = BOT) -> str:
    return f"/openapi/v1/bots/sessions/{bot}"


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
    ("sessions", f"/openapi/v1/bots/sessions/{BOT}"),
    ("engine", f"/openapi/v1/bots/engine/{BOT}/capabilities"),
    ("models", f"/openapi/v1/bots/models/{BOT}"),
    ("approvals", f"/openapi/v1/bots/approvals/{BOT}/mode?session_key=k"),
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
            data={"supported": [], "sessionKey": "k", "mode": "approve", "id": "s"}
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

    resp = client.get(
        f"/openapi/v1/bots/connection/{BOT}", params={"stage": "online"}
    )
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
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


def _operations(schema: dict):
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                yield path, method, operation


def _query_params(operation: dict) -> dict[str, dict]:
    return {
        p["name"]: p
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    }


def test_owner_id_and_stage_are_on_exactly_the_engine_runtime_operations():
    schema = _schema()
    carrying, engine_runtime = [], []
    for path, method, operation in _operations(schema):
        params = _query_params(operation)
        if path.startswith(_ENGINE_RUNTIME_PREFIXES):
            engine_runtime.append((method, path))
            assert "owner_id" in params, f"{method.upper()} {path} lacks owner_id"
            assert "stage" in params, f"{method.upper()} {path} lacks stage"
            assert not params["owner_id"].get("required", False), path
            assert not params["stage"].get("required", False), path
        if "owner_id" in params or "stage" in params:
            carrying.append((method, path))
    assert len(engine_runtime) == 16
    assert sorted(carrying) == sorted(engine_runtime)


def test_the_stage_enum_publishes_exactly_the_three_runtimes():
    schema = _schema()
    enum = schema["components"]["schemas"]["RuntimeStage"]["enum"]
    assert sorted(enum) == ["draft", "online", "verify"]
    assert [m.value for m in RuntimeStage] == ["draft", "verify", "online"]


def test_neither_parameter_is_ever_a_body_field_or_a_path_segment():
    """Scoped to the sixteen operations' request bodies and addresses — a
    future *response* model elsewhere on the surface may legitimately expose
    an ``owner_id`` or ``stage`` field; the placement rule is about where
    these two request parameters travel."""
    schema = _schema()
    components = schema.get("components", {}).get("schemas", {})
    for path, method, operation in _operations(schema):
        if not path.startswith(_ENGINE_RUNTIME_PREFIXES):
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
        if path.startswith(_ENGINE_RUNTIME_PREFIXES):
            assert "409" in operation.get("responses", {}), (
                f"{method.upper()} {path} does not document 409"
            )
