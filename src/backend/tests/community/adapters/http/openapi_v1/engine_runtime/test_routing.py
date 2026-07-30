"""Mounting and path invariants for the engine-runtime groups (Task 11)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.openapi_v1 import (
    _ENGINE_RUNTIME_GROUPS,
    PUBLIC_API_PREFIX,
    build_public_router,
)

#: sessions 7 + engine 3 + models 2 + approvals 3 + connection 1
_EXPECTED_ROUTE_COUNT = 16

_BOTS_PREFIX = f"{PUBLIC_API_PREFIX}/bots/"


def _engine_runtime_routes() -> list[APIRoute]:
    return [r for g in _ENGINE_RUNTIME_GROUPS for r in g.routes if isinstance(r, APIRoute)]


def _document() -> dict:
    """The generated OpenAPI document.

    Route introspection goes through this rather than ``app.routes``: FastAPI
    wraps an included router in an ``_IncludedRouter`` instead of flattening its
    endpoints, so walking ``.routes`` finds none of them. The document is also
    the thing external callers actually receive, which makes it the better
    subject for a contract assertion.
    """
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


def _schema_path(path: str) -> str:
    """Path as the generated document spells it (converters are stripped)."""
    return path.replace(":path", "")


def test_every_route_begins_with_the_bots_prefix():
    """The gateway forwards to agentclaw on this prefix.

    A route mounted anywhere else is unreachable in production and the mistake
    is invisible until deploy — so it is asserted rather than reviewed.
    """
    offenders = [
        r.path for r in _engine_runtime_routes() if not r.path.startswith(_BOTS_PREFIX)
    ]
    assert not offenders, f"routes outside {_BOTS_PREFIX}: {offenders}"


def test_every_route_is_scoped_to_a_single_bot():
    offenders = [
        r.path
        for r in _engine_runtime_routes()
        if not r.path.startswith(f"{_BOTS_PREFIX}{{bot_id}}")
    ]
    assert not offenders, f"routes not bot-scoped: {offenders}"


def test_the_surface_is_the_agreed_size():
    """A drifting count means a group was added or dropped without a decision."""
    assert len(_engine_runtime_routes()) == _EXPECTED_ROUTE_COUNT


def test_groups_are_mounted_and_reachable_in_the_public_router():
    paths = set(_document()["paths"])
    for expected in (
        f"{_BOTS_PREFIX}{{bot_id}}/sessions",
        f"{_BOTS_PREFIX}{{bot_id}}/engine/capabilities",
        f"{_BOTS_PREFIX}{{bot_id}}/models",
        f"{_BOTS_PREFIX}{{bot_id}}/approvals/mode",
        f"{_BOTS_PREFIX}{{bot_id}}/connection",
    ):
        assert expected in paths, f"{expected} not mounted"


def test_literal_groups_are_registered_before_the_bot_id_wildcard():
    """``/openapi/v1/bots/mcp`` must not be swallowed by ``{bot_id}``.

    A pre-existing Track B invariant, re-asserted because Track C inserts five
    routers into the same assembly. Registration order is what decides it, and
    the document preserves it.
    """
    order = list(_document()["paths"])
    assert order.index(f"{_BOTS_PREFIX}mcp/servers") < order.index(
        f"{_BOTS_PREFIX}{{bot_id}}"
    )


def test_engine_runtime_routes_document_501_and_504():
    """And the rest of the surface does not — they cannot return them."""
    schema = _document()

    runtime_paths = {_schema_path(r.path) for r in _engine_runtime_routes()}
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            documented = set(operation["responses"])
            extras = {"501", "504"} & documented
            if path in runtime_paths:
                assert extras == {"501", "504"}, f"{method.upper()} {path} missing {extras}"
            else:
                assert not extras, f"{method.upper()} {path} advertises {extras}"


def test_enums_reach_the_generated_document_as_string_enums():
    """Catches a model annotating an enum while Pydantic emits a bare string."""
    components = _document()["components"]["schemas"]

    for name, expected in (
        ("SocketKind", {"chat"}),
        ("ApprovalMode", {"approve", "on-miss", "never"}),
        ("MessageRole", {"user", "assistant", "system", "tool_use", "tool_result"}),
    ):
        assert name in components, f"{name} absent from the published document"
        component = components[name]
        assert component["type"] == "string"
        assert set(component["enum"]) == expected
        assert component["x-enum-descriptions"]
