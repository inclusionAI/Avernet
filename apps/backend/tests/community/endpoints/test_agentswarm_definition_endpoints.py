"""Per-endpoint coverage for the agentswarm agent definition surface.

The definitions routes are all POST with the action in the path, and they are
per-user: every call derives its owner from the authenticated caller. These
cases cover each route's happy path plus the error it can actually return.

Ownership is the interesting axis here. A definition owned by someone else must
answer exactly like one that never existed, so the "other owner" cases assert a
404 rather than a 403 — asserting 403 would re-introduce the enumeration oracle
the surface deliberately refuses.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.api.agentswarm_service import AgentSwarmServiceProtocol
from agentclaw.community.core.agentswarm.domain import models as dm
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/openapi/v1/agentswarm/agent/definitions"
_TENANT = "endpoint-test"
_OWNER = "staff-owner"
_OTHER = "staff-other"
_DEF_ID = "endpointtestdefinition0000000000000"
_DEF_ROW_HINT = 1
_OTHER_DEF_ID = "endpointtestotherdef000000000000000"
_MANIFEST = (
    "schema_version: 1\n"
    "manifest:\n"
    "  identity:\n"
    "    - type: SOUL.md\n"
    "      content: 'You are an endpoint-test greeter.'\n"
    "  mcp: []\n  resources: []\n  skills: []\n  cli_tools: []\n"
    "script:\n"
    "  body: 'echo endpoint-test'\n"
)


def _service(world: Any) -> AgentSwarmServiceProtocol:
    """The same service instance the endpoint resolves."""
    return world.get(AgentSwarmServiceProtocol)


def _repo(world: Any) -> Any:
    from agentclaw.community.core.agentswarm.repository.agent_definition import (
        AgentDefinitionRepository,
    )
    return world.get(AgentDefinitionRepository)


def _seed_definition(world: Any, *, owner: str = _OWNER,
                     name: str = "seed_reviewer",
                     definition_id: str = _DEF_ID) -> dm.AgentDefinition:
    """Create a live definition with a FIXED id, so a case can name it.

    ``create_definition`` mints a UUID, which a decorator cannot know at import
    time; seeding through the repository keeps the id a constant the case can
    reference.
    """
    return _repo(world).create(dm.AgentDefinition(
        definition_id=dm.AgentDefinitionId(definition_id),
        tenant=dm.TenantId(_TENANT),
        owner_id=owner,
        creator_id=owner,
        modifier_id=owner,
        name=name,
        role="checks things",
        instructions="Respond with exactly the single word: hello",
        kind=dm.AgentKind.LLM,
    ))


def _seed_bot_definition(world: Any) -> dm.AgentDefinition:
    """A bot-kind definition carrying a real manifest."""
    return _service(world).create_definition(
        tenant=_TENANT,
        owner_id=_OWNER,
        name="seed_bot",
        role="greets politely",
        kind=dm.AgentKind.BOT,
        engine="openclaw",
        bot_type="personal",
        manifest_document=_MANIFEST,
    )


# The ``test`` deploy profile binds the LOCAL auth plugin, which resolves
# identity from a ``staff_id`` cookie or an ``x-user-id`` header — no JWT. The
# definitions routes derive their owner from that principal, so every case
# carries it and every seed enables the same plugin the request will use.
_HEADERS = {"x-user-id": _OWNER}
_OTHER_HEADERS = {"x-user-id": _OTHER}


def _seed_with_auth(world: Any, **kw: Any) -> dm.AgentDefinition:
    """Seed one live definition owned by ``_OWNER``."""
    return _seed_definition(world, **kw)


_QUERY = {"tenant": _TENANT}


# ── POST /agent/definitions/add ─────────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/add",
    scenario="ok",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"name": "reviewer", "role": "checks things", "kind": "llm"},
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "name": "reviewer",
            "kind": "llm",
            "owner_id": _OWNER,
            "creator_id": _OWNER,
            "modifier_id": _OWNER,
        },
    ),
)
def add_definition_ok():
    """Body intentionally empty — the framework owns invocation."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/add",
    scenario="duplicate_name",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"name": "dup_reviewer", "role": "checks things"},
    ),
    seed=lambda world: _seed_with_auth(world, name="dup_reviewer"),
    expect=ExpectError(status=409),
)
def add_definition_duplicate_name():
    pass


# ── POST /agent/definitions/list ────────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/list",
    scenario="ok",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body={}),
    seed=_seed_with_auth,
    expect=ExpectSuccess(
        status=200,
        json_contains={"page": 1, "total": 1},
    ),
)
def list_definitions_ok():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/list",
    scenario="wrong_page_size",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body={"page_size": 500}),
    expect=ExpectError(status=422),
)
def list_definitions_wrong_page_size():
    pass


# ── POST /agent/definitions/get ─────────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/get",
    scenario="ok",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"definition_id": _DEF_ID},
    ),
    seed=_seed_with_auth,
    expect=ExpectSuccess(
        status=200,
        json_contains={"name": "seed_reviewer", "owner_id": _OWNER},
    ),
)
def get_definition_ok():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/get",
    scenario="not_found",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body={"definition_id": "missing"}),
    expect=ExpectError(status=404),
)
def get_definition_not_found():
    pass


# ── POST /agent/definitions/versions ────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/versions",
    scenario="ok",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"definition_id": _DEF_ID},
    ),
    seed=_seed_with_auth,
    expect=ExpectSuccess(
        status=200,
        json_contains={"deleted": False},
    ),
)
def list_versions_ok():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/versions",
    scenario="not_found",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body={"definition_id": "missing"}),
    expect=ExpectError(status=404),
)
def list_versions_not_found():
    pass


# ── POST /agent/definitions/update ──────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/update",
    scenario="ok",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "definition_id": _DEF_ID,
            "definition_row_id": _DEF_ROW_HINT,
            "name": "seed_reviewer",
            "role": "revised role",
            "kind": "llm",
        },
    ),
    seed=_seed_with_auth,
    expect=ExpectSuccess(status=200, json_contains={"role": "revised role"}),
)
def update_definition_ok():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/update",
    scenario="stale_version",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "definition_id": _DEF_ID,
            "definition_row_id": 999999,
            "name": "seed_reviewer",
            "role": "should not land",
        },
    ),
    seed=_seed_with_auth,
    expect=ExpectError(status=409),
)
def update_definition_stale_version():
    pass


# ── POST /agent/definitions/delete ──────────────────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/delete",
    scenario="ok",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={"definition_id": _DEF_ID},
    ),
    seed=_seed_with_auth,
    expect=ExpectSuccess(status=200, json_contains={"retired": 1}),
)
def delete_definition_ok():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/delete",
    scenario="not_found",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS, json_body={"definition_id": "missing"}),
    expect=ExpectError(status=404),
)
def delete_definition_not_found():
    pass


# ── ownership: another user's definition is indistinguishable from absent ───

@endpoint_test(
    method="POST",
    path=f"{_BASE}/get",
    scenario="other_owner_is_not_found",
    input=CaseInput(
        query_params=_QUERY,
        # The owner asks for the other user's definition: 404, never 403.
        headers=_HEADERS,
        json_body={"definition_id": _OTHER_DEF_ID},
    ),
    seed=lambda world: _seed_definition(
        world, owner=_OTHER, name="theirs", definition_id=_OTHER_DEF_ID,
    ),
    expect=ExpectError(status=404),
)
def get_definition_other_owner_is_not_found():
    """404, never 403: a 403 would confirm the id exists."""


# ── bot kind: the manifest is validated at write ────────────────────────────

@endpoint_test(
    method="POST",
    path=f"{_BASE}/add",
    scenario="bot_with_manifest",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "name": "manifest_bot",
            "role": "greets politely",
            "kind": "bot",
            "engine": "openclaw",
            "bot_type": "personal",
            "manifest_document": _MANIFEST,
        },
    ),
    expect=ExpectSuccess(
        status=201,
        json_contains={"kind": "bot", "engine": "openclaw"},
    ),
)
def add_bot_definition_with_manifest():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/add",
    scenario="invalid_manifest",
    input=CaseInput(
        query_params=_QUERY,
        headers=_HEADERS,
        json_body={
            "name": "bad_manifest_bot",
            "role": "r",
            "kind": "bot",
            "manifest_document": "schema_version: 1\nmanifest: [not a mapping",
        },
    ),
    expect=ExpectError(status=422),
)
def add_bot_definition_invalid_manifest():
    pass