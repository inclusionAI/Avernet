"""Mounting and path invariants for the engine-runtime groups (Task 11)."""

from __future__ import annotations

from fastapi.routing import APIRoute

from agentclaw.community.adapters.http.openapi_v1 import (
    _ENGINE_RUNTIME_GROUPS,
    PUBLIC_API_PREFIX,
)
from tests.community.adapters.http.openapi_v1.conftest import public_document

#: sessions 10 + session files 6 + engine 4 + models 2 + nodes 1 + approvals 3 + connection 1
_EXPECTED_ROUTE_COUNT = 27

_BOTS_PREFIX = f"{PUBLIC_API_PREFIX}/bots/"


def _engine_runtime_routes() -> list[APIRoute]:
    """The current engine-runtime routes."""
    return [
        r for g in _ENGINE_RUNTIME_GROUPS for r in g.routes if isinstance(r, APIRoute)
    ]


def _all_engine_runtime_paths() -> set[str]:
    """Current *and* retiring, for the assertions about the response table.

    The legacy addresses are mounted with the same table and answer with the
    same handlers, so they document the 501 and 504 too — as they must, or the
    old address would stop describing what it returns.
    """
    from agentclaw.community.adapters.http.openapi_v1.deprecated import (
        ENGINE_RUNTIME_GROUPS as LEGACY,
    )

    return {
        _schema_path(r.path)
        for g in [*_ENGINE_RUNTIME_GROUPS, *LEGACY]
        for r in g.routes
        if isinstance(r, APIRoute)
    }


def _document() -> dict:
    """The generated OpenAPI document.

    Route introspection goes through this rather than ``app.routes``: FastAPI
    wraps an included router in an ``_IncludedRouter`` instead of flattening its
    endpoints, so walking ``.routes`` finds none of them. The document is also
    the thing external callers actually receive, which makes it the better
    subject for a contract assertion.
    """
    return public_document()


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


#: Each group's component segment, which its paths must name after ``{bot_id}``.
_COMPONENTS = ("sessions", "engine", "models", "nodes", "approvals", "connection")


def test_every_route_names_the_bot_then_its_component():
    """The surface's addressing rule: an operation on one bot starts with it.

    Every path here is ``/openapi/v1/bots/{bot_id}/<component>/…``. The bot
    comes first because that is what the operation acts on; the component
    follows because it says which part of the bot.

    These five have now been addressed three ways. They began as
    ``{bot_id}/<component>``, were normalized to ``<component>/{bot_id}``, and
    are back to bot-first — this time with the resources, routines and skills
    groups joining them rather than staying on a query parameter, which is what
    makes it a rule rather than a preference. The reasoning is in
    ``specs/2026-08-15-openapi-v1-bot-first-addressing/spec.md``; the short of
    it is that component-first put every component name in the segment a bot id
    is read from, which is what made fifteen names unusable as bot ids.
    """
    offenders = [
        r.path
        for r in _engine_runtime_routes()
        if not any(
            r.path.startswith(f"{_BOTS_PREFIX}{{bot_id}}/{component}")
            for component in _COMPONENTS
        )
    ]
    assert not offenders, f"routes not {{bot_id}}/<component>-shaped: {offenders}"


def test_the_surface_is_the_agreed_size():
    """A drifting count means a group was added or dropped without a decision."""
    assert len(_engine_runtime_routes()) == _EXPECTED_ROUTE_COUNT


def test_groups_are_mounted_and_reachable_in_the_public_router():
    paths = set(_document()["paths"])
    for expected in (
        f"{_BOTS_PREFIX}{{bot_id}}/sessions",
        f"{_BOTS_PREFIX}{{bot_id}}/engine/capabilities",
        f"{_BOTS_PREFIX}{{bot_id}}/models",
        f"{_BOTS_PREFIX}{{bot_id}}/nodes",
        f"{_BOTS_PREFIX}{{bot_id}}/approvals/mode",
        f"{_BOTS_PREFIX}{{bot_id}}/connection",
    ):
        assert expected in paths, f"{expected} not mounted"


def test_session_file_upload_intents_are_published_on_the_openapi_surface():
    """Files are a new capability on the bot-first Sessions surface."""
    assert (
        f"{_BOTS_PREFIX}{{bot_id}}/sessions/{{session_id}}/files/upload-intents"
        in _document()["paths"]
    )


def test_literal_groups_are_registered_before_the_bot_id_wildcard():
    """A single-segment literal must not be swallowed by ``{bot_id}``.

    Bot-first addressing shrank this invariant rather than removing it.
    ``resources`` and ``routines`` used to be the cases that depended on it —
    they served their own collection roots one segment under
    ``/openapi/v1/bots``, exactly where the bots wildcard also matches. Both now
    sit beneath ``{bot_id}``, so ordering no longer decides their fate.

    What still occupies that segment is the set of operations with no single
    bot to address: the account-level reads and the two groups that are not
    bot-scoped. They are the reason the rule survives, and the reason the
    reserved-name list is not empty.
    """
    order = list(_document()["paths"])
    wildcard = order.index(f"{_BOTS_PREFIX}{{bot_id}}")
    for literal in (
        "check-name",
        "ceiling",
        "authorized",
        "mcp/servers",
        "logs/traces",
    ):
        assert order.index(f"{_BOTS_PREFIX}{literal}") < wildcard, literal


def test_engine_runtime_routes_document_501_and_504():
    """And the rest of the surface does not — they cannot return them."""
    schema = _document()

    runtime_paths = _all_engine_runtime_paths()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            documented = set(operation["responses"])
            extras = {"501", "504"} & documented
            if path in runtime_paths:
                assert extras == {"501", "504"}, (
                    f"{method.upper()} {path} missing {extras}"
                )
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
