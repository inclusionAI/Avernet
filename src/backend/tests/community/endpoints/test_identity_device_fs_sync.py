"""Golden-master device-push tests for the identity (persona) write routes.

Pins the **DeviceFileSystemPlugin write** the identity PUT routes perform
on an Arca-backed bot. The arca_utils→plugin refactor (spec
2026-06-05-resources-identity-device-fs-writes) routed the persona write
through the boundary; Task 14 must keep it ARCA-equivalent, so these
record the exact effect against current code and must pass **unchanged**
after the migration.

Seam (project rule — substitute only the system-boundary plugin): the
handlers' ``_write_file_safely`` takes the Arca branch when the bot's
binding reports ``device_provider="arca"`` (real arca binding), then
resolves the device fs via ``DeviceFilesystemDispatcher.for_bot(...)``.
The dispatcher routes through the real ``DeviceAccessor`` boundary; we drive
its ``LocalDeviceAccessor`` (MockSeam) ``get_connection_info`` to report a
``local`` provider so ``for_bot`` returns the real ``LocalDeviceFileSystem``,
which performs the real write. The assertion reads the persona file off
disk (same path + bytes the Arca push would carry).

A non-reference file (``SOUL.md``) is used so the write is a single push
(reference files would additionally re-sync AGENTS.md).

Coverage:
- bot-level write   (PUT .../bot/{bot_id}/{file_type}): happy + error (bad file_type → 400)
- entity-level write(PUT .../{entity_type}/{entity_id}/{file_type}): happy + error (bad file_type → 400)

The ``/batch`` write routes are intentionally NOT covered here: they are
shadowed by the ``{file_type}`` routes declared before them (a PUT to
``.../bot/{bot_id}/batch`` matches ``.../bot/{bot_id}/{file_type}`` with
``file_type="batch"``), so they are unreachable via HTTP — a pre-existing
routing bug tracked separately.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from tests.community.framework.device_seams import route_device_accessor_to_local
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_arca_device


def _get_bot_file_path(entity_type, entity_id, bot_id, file_type, pf, engine="openclaw"):
    """Local path composer (replaces the old router helper of the same name)."""
    d = pf.get_bot_engine_dir(entity_id, bot_id, engine, entity_type)
    if engine == "openclaw":
        d = d / "workspace"
    return d / file_type


def _get_entity_file_path(entity_type, entity_id, file_type, pf, engine="openclaw"):
    """Local entity path composer (replaces the old router helper of the same name)."""
    return pf.get_entity_identity_dir(entity_id, entity_type, engine) / file_type
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_OWNER = "u_owner"
_DEVICE_ID = "arca_dev_test"
_ENGINE = "openclaw"
_FILE = "SOUL.md"  # non-reference → single write, no AGENTS.md re-sync
_CONTENT = "I am a helpful, careful bot."


def _route_for_bot_to_local(world) -> None:
    """Drive the real DeviceAccessor boundary so the dispatcher's ``for_bot``
    returns the real ``LocalDeviceFileSystem`` (the boundary we can run in
    tests), which performs the real write we assert off disk.
    """
    # LocalDeviceAccessor is a plain core class now (not a MockSeam); drive its
    # boundary via the framework seam helper (guard-compliant — no mock-on-
    # world.get assignment inside tests/endpoints/).
    route_device_accessor_to_local(world)


def _seed_arca_bot(world, *, bot_id: str) -> None:
    make_staff_user(world, user_id=_OWNER)
    binding_id = make_active_arca_device(world, owner_id=_OWNER, device_id=_DEVICE_ID)
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
            "active_engine": _ENGINE,
            "device_id": _DEVICE_ID,
            # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
            "binding_id": binding_id,
        }
    )
    _route_for_bot_to_local(world)


def _pf(world):
    return world.get(WorkspacePathFactory)


# ============================================================
# bot-level write  (PUT .../bot/{bot_id}/{file_type})
# ============================================================

def _assert_bot_persona_written(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    path = _get_bot_file_path("staff", _OWNER, "bot_id_persona", _FILE, _pf(world))
    assert path.read_bytes() == _CONTENT.encode("utf-8"), path


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="happy_arca_bot_persona_write",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": "bot_id_persona",
            "file_type": _FILE,
        },
        headers={"x-user-id": _OWNER},
        json_body={"content": _CONTENT},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_id_persona"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_bot_persona_written,),
)
def bot_persona_write_pushes_to_device():
    """Writing a bot persona file pushes the exact content to the device path."""


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}",
    scenario="error_invalid_file_type_400",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "bot_id": "bot_id_persona_err",
            "file_type": "BOGUS.md",
        },
        headers={"x-user-id": _OWNER},
        json_body={"content": _CONTENT},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_id_persona_err"),
    expect=ExpectError(status=400),
)
def bot_persona_invalid_file_type_400():
    """An invalid identity file_type → 400 before any device write."""


# ============================================================
# entity-level write  (PUT .../{entity_type}/{entity_id}/{file_type})
# ============================================================

def _assert_entity_persona_written(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    path = _get_entity_file_path("staff", _OWNER, _FILE, _pf(world))
    assert path.read_bytes() == _CONTENT.encode("utf-8"), path


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/{file_type}",
    scenario="happy_arca_entity_persona_write",
    input=CaseInput(
        path_params={"entity_type": "staff", "entity_id": _OWNER, "file_type": _FILE},
        headers={"x-user-id": _OWNER},
        json_body={"content": _CONTENT},
    ),
    # entity-level write resolves the device through bot_id="default", owned by
    # the entity — seed that bot with the arca binding.
    seed=lambda world: _seed_arca_bot(world, bot_id="default"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_entity_persona_written,),
)
def entity_persona_write_pushes_to_device():
    """Entity-level persona write pushes content to the entity's device path."""


@endpoint_test(
    method="PUT",
    path="/api/identity/{entity_type}/{entity_id}/{file_type}",
    scenario="error_invalid_file_type_400",
    input=CaseInput(
        path_params={
            "entity_type": "staff",
            "entity_id": _OWNER,
            "file_type": "BOGUS.md",
        },
        headers={"x-user-id": _OWNER},
        json_body={"content": _CONTENT},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="default"),
    expect=ExpectError(status=400),
)
def entity_persona_invalid_file_type_400():
    """An invalid entity identity file_type → 400 before any device write."""
