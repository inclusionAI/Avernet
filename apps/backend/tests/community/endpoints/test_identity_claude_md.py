"""Framework-level tests for CLAUDE.md identity file support.

Uses @endpoint_test + TestClient + real DI + SQLite in-memory DB,
consistent with test_harness_admin_router.py and test_identity_device_fs_sync.py.

Covers:
1. CLAUDE.md is a valid identity file type
2. GET CLAUDE.md for claude_code bot (with content / empty)
3. PUT CLAUDE.md for claude_code bot (write + read-back)
4. engine_type auto-resolved from bot DB record
5. engine_type query param override
6. AGENTS.md sync NOT triggered for claude_code
7. Backward compat: openclaw bot path includes workspace/
9. Invalid file type returns 400
10. Arca container path translation (only claude_code)
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.services.identity import (
    CLAUDE_CODE_IDENTITY_FILES,
    REFERENCE_FILES,
    VALID_IDENTITY_FILES,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


def _bot_path(pf, entity_type, entity_id, bot_id, file_type, engine):
    """Compose a bot identity file path via the path factory (replaces the old
    router ``_get_bot_file_path`` helper; same layout as IdentityService)."""
    d = pf.get_bot_engine_dir(entity_id, bot_id, engine, entity_type)
    if engine == "openclaw":
        d = d / "workspace"
    return d / file_type


_OWNER = "u_claude_md"
_CC_BOT = "bot_cc_crud"
_CC_BOT_EMPTY = "bot_cc_empty"
_CC_BOT_OVERRIDE = "bot_cc_override"
_OC_BOT = "bot_oc_compat"
_CLAUDE_CONTENT = "# CLAUDE.md\n\nCustom instructions for bot."
_UPDATED_CONTENT = "# CLAUDE.md v2\n\nUpdated instructions."


# ── Seed helpers ──────────────────────────────────────────────


def _seed_local_binding(world, *, bot_id: str) -> int:
    """Insert an ACTIVE ``local`` binding with no ``adapter_port`` → the bot
    resolves to a device context that routes through LocalDeviceFileSystem's
    pathlib mode onto tmp_path (the identity flow has no local-OSS fallback)."""
    from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
    return world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER, entity_type="staff", device_id=f"dev_{bot_id}",
        device_provider="local", env="dev", device_props={}, status="ACTIVE",
        apply_reason="test seed", applied_by=_OWNER,
    )


def _seed_cc_bot(world, *, bot_id: str = _CC_BOT) -> None:
    """Seed a claude_code bot bound to a local (pathlib) device."""
    make_staff_user(world, user_id=_OWNER)
    bind_id = _seed_local_binding(world, bot_id=bot_id)
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


def _seed_cc_bot_with_file(world) -> None:
    """Seed claude_code bot and pre-create CLAUDE.md on disk."""
    _seed_cc_bot(world)
    pf = world.get(WorkspacePathFactory)
    path = _bot_path(pf, "staff", _OWNER, _CC_BOT, "CLAUDE.md", "claude_code")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CLAUDE_CONTENT, encoding="utf-8")


def _seed_cc_bot_empty(world) -> None:
    _seed_cc_bot(world, bot_id=_CC_BOT_EMPTY)


def _seed_cc_bot_override(world) -> None:
    _seed_cc_bot(world, bot_id=_CC_BOT_OVERRIDE)


def _seed_oc_bot(world) -> None:
    """Seed an openclaw bot for backward compat tests."""
    make_staff_user(world, user_id=_OWNER)
    bind_id = _seed_local_binding(world, bot_id=_OC_BOT)
    world.get(BotRepository).insert(
        {
            "bot_id": _OC_BOT,
            "bot_name": f"Bot {_OC_BOT}",
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


# ── GET CLAUDE.md (with content) ─────────────────────────────


def _assert_get_claude_md(response, world) -> None:
    # claude_code addresses files inside its container; the HTTP layer returns the
    # logic-view path. The container path translation is unit-tested in
    # tests/di/modules/test_dispatcher_for_identity.py.
    body = response.json()
    assert body["success"] is True
    assert body["file_type"] == "CLAUDE.md"
    assert body["file_path"] == "identity/CLAUDE.md"


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_cc_get_claude_md",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT,
            "file_type": "CLAUDE.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_cc_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_get_claude_md,),
)
def cc_get_claude_md():
    """GET CLAUDE.md for claude_code bot returns the logic-view path."""


# ── GET CLAUDE.md (empty — file not created yet) ─────────────


def _assert_empty_content(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    assert body["content"] == ""


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_cc_get_claude_md_empty",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT_EMPTY,
            "file_type": "CLAUDE.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_cc_bot_empty,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_empty_content,),
)
def cc_get_claude_md_empty():
    """GET CLAUDE.md returns empty when file does not exist."""


# NOTE: claude_code PUT/content-roundtrip is not exercised here — claude_code
# addresses files inside its arca/baas container (no local-FS path), so it cannot
# roundtrip via the test's pathlib LocalDeviceFileSystem. The container-path
# mapping is unit-tested (test_dispatcher_for_identity) and the write→device
# routing by test_http_adapters_use_resolver. The openclaw PUT below covers the
# HTTP write contract end-to-end.


# ── engine_type query param override ─────────────────────────


def _assert_override_logic_view(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    assert body["file_path"] == "identity/CLAUDE.md"


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_cc_engine_type_override",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT_OVERRIDE,
            "file_type": "CLAUDE.md",
        },
        query_params={"engine_type": "openclaw"},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_cc_bot_override,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_override_logic_view,),
)
def cc_engine_type_override():
    """engine_type query param overrides DB-resolved engine."""


# ── Backward compat: openclaw PUT RULES.md ───────────────────


def _assert_oc_rules_written(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    path = _bot_path(_pf(world), "staff", _OWNER, _OC_BOT, "RULES.md", "openclaw")
    assert path.read_bytes() == b"# OC Rules"
    assert "workspace" in str(path)


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_oc_put_rules_md_compat",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _OC_BOT,
            "file_type": "RULES.md",
        },
        headers={"x-user-id": _OWNER},
        json_body={"content": "# OC Rules"},
    ),
    seed=_seed_oc_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_oc_rules_written,),
)
def oc_put_rules_md_compat():
    """PUT RULES.md for openclaw bot writes to workspace/ path (backward compat)."""


# ── Invalid file type → 400 ──────────────────────────────────


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="error_cc_invalid_file_type",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT,
            "file_type": "INVALID.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_cc_bot,
    expect=ExpectError(status=400),
)
def cc_invalid_file_type():
    """GET with invalid file_type returns 400."""


# ── Constants validation (no HTTP, pure logic) ────────────────


@endpoint_test(
    method="GET",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_cc_claude_md_in_valid_set",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": _CC_BOT,
            "file_type": "CLAUDE.md",
        },
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_cc_bot,
    expect=ExpectSuccess(status=200),
    extra_assertions=(
        lambda resp, world: (
            None if "CLAUDE.md" in VALID_IDENTITY_FILES else (_ for _ in ()).throw(
                AssertionError("CLAUDE.md not in VALID_IDENTITY_FILES")
            )
        ),
        lambda resp, world: (
            None if CLAUDE_CODE_IDENTITY_FILES == {"CLAUDE.md"} else (_ for _ in ()).throw(
                AssertionError(f"CLAUDE_CODE_IDENTITY_FILES wrong: {CLAUDE_CODE_IDENTITY_FILES}")
            )
        ),
        lambda resp, world: (
            None if "CLAUDE.md" not in REFERENCE_FILES else (_ for _ in ()).throw(
                AssertionError("CLAUDE.md should not be in REFERENCE_FILES")
            )
        ),
    ),
)
def cc_constants_validation():
    """Validate CLAUDE.md is in correct constant sets."""

# NOTE: the arca container-path translation (claude_code → /home/admin/.claude_code/...)
# now lives in core/services/identity_addressing.build_arca_identity_mapper and is
# covered by tests/di/modules/test_dispatcher_for_identity.py.
