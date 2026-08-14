"""Framework-level integration tests for CLAUDE.md CRUD on claude_code engine.

Uses the same @endpoint_test + TestClient + real DI + real SQLite pattern as
test_identity_device_fs_sync.py and test_harness_admin_router.py.

Covers:
- GET /api/identity/.../bot/{bot_id}/CLAUDE.md for claude_code bot
- PUT /api/identity/.../bot/{bot_id}/CLAUDE.md for claude_code bot
- Backward compat: openclaw bot still works with AGENTS.md
- Invalid file type returns 400
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.devices.services.local_device_accessor import LocalDeviceAccessor
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_arca_device
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


def _get_bot_file_path(entity_type, entity_id, bot_id, file_type, pf, engine):
    """Compose a bot identity file path via the path factory (replaces the old
    router helper of the same name; same layout as IdentityService)."""
    d = pf.get_bot_engine_dir(entity_id, bot_id, engine, entity_type)
    if engine == "openclaw":
        d = d / "workspace"
    return d / file_type


_OWNER = "u_cc_owner"
_DEVICE_ID = "arca_cc_dev"
_CC_BOT = "bot_claude_code"
_OC_BOT = "bot_openclaw"
_CLAUDE_CONTENT = "# My CLAUDE.md\n\nCustom instructions."
_RULES_CONTENT = "# Rules for openclaw bot"


def _route_for_bot_to_local(world) -> None:
    world.get(LocalDeviceAccessor).set_response(
        "get_connection_info", {"device_provider": "local"}
    )


def _seed_claude_code_bot(world, *, bot_id: str = _CC_BOT) -> None:
    """Seed claude_code bot bound to a local (pathlib) device."""
    make_staff_user(world, user_id=_OWNER)
    bind_id = make_active_arca_device(world, owner_id=_OWNER, device_id=f"dev_{bot_id}")
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "active_engine": "claude_code",
            "binding_id": bind_id,
        }
    )


def _seed_openclaw_bot(world, *, bot_id: str = _OC_BOT) -> None:
    """Seed openclaw bot bound to a local (pathlib) device."""
    make_staff_user(world, user_id=_OWNER)
    bind_id = make_active_arca_device(world, owner_id=_OWNER, device_id=_DEVICE_ID)
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "active_engine": "openclaw",
            "binding_id": bind_id,
        }
    )


def _pf(world):
    return world.get(WorkspacePathFactory)


# NOTE: claude_code PUT / content-roundtrip is not exercised end-to-end here —
# claude_code addresses files inside its arca/baas container, so it cannot roundtrip
# via the test's pathlib LocalDeviceFileSystem. The container-path mapping is
# unit-tested (test_dispatcher_for_identity); the openclaw PUT below covers the HTTP
# write contract end-to-end, and the GET below covers the HTTP read contract.


# ============================================================
# GET CLAUDE.md for claude_code bot — logic-view contract
# ============================================================

def _assert_claude_md_read(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    assert body["file_type"] == "CLAUDE.md"
    assert body["file_path"] == "identity/CLAUDE.md"


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_claude_code_get_claude_md",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT,
            "file_type": "CLAUDE.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_claude_code_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_claude_md_read,),
)
def claude_code_get_claude_md():
    """GET CLAUDE.md for a claude_code bot returns the logic-view path."""


# ============================================================
# GET CLAUDE.md returns empty when file does not exist
# ============================================================

_CC_BOT_EMPTY = "bot_cc_empty"


def _seed_claude_code_bot_no_file(world) -> None:
    _seed_claude_code_bot(world, bot_id=_CC_BOT_EMPTY)


def _assert_empty_content(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    assert body["content"] == ""


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_claude_code_get_claude_md_empty",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT_EMPTY,
            "file_type": "CLAUDE.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_claude_code_bot_no_file,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_empty_content,),
)
def claude_code_get_claude_md_empty():
    """GET CLAUDE.md returns empty content when file does not exist yet."""


# ============================================================
# Backward compat: openclaw bot PUT AGENTS.md still works
# ============================================================

def _assert_agents_md_written(response, world) -> None:
    assert response.json().get("success") is True
    path = _get_bot_file_path("staff", _OWNER, _OC_BOT, "RULES.md", _pf(world), "openclaw")
    assert path.read_bytes() == _RULES_CONTENT.encode("utf-8")
    assert "workspace" in str(path), f"openclaw path should contain workspace/: {path}"


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_openclaw_put_rules_md_compat",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _OC_BOT,
            "file_type": "RULES.md",
        },
        headers={"x-user-id": _OWNER},
        json_body={"content": _RULES_CONTENT},
    ),
    seed=_seed_openclaw_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_agents_md_written,),
)
def openclaw_put_rules_md_compat():
    """PUT RULES.md for openclaw bot still writes to workspace/ path (backward compat)."""


# ============================================================
# Invalid file type returns 400
# ============================================================

@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="error_claude_code_invalid_file_type",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT,
            "file_type": "INVALID.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_claude_code_bot,
    expect=ExpectError(status=400),
)
def claude_code_invalid_file_type():
    """GET with invalid file_type returns 400 for claude_code bot."""
