"""Golden-master device-push tests for the resources file entrypoints.

Pins the **DeviceFileSystemPlugin write/delete** the resource file routes
perform on an Arca-backed bot. The arca_utils→plugin refactor (spec
2026-06-05-resources-identity-device-fs-writes) routed these pushes
through the boundary; Task 14 must keep them ARCA-equivalent, so these
record the exact effect against current code and must pass **unchanged**
after the migration.

Seam (per the project's hard requirement — the only thing a test may
substitute is the **system boundary plugin**, never internal plumbing
like the dispatcher): the routes select the Arca *branch* from the bot's
device binding (``arca_utils.get_device_info`` → ``"arca"``), then resolve
the device fs via ``DeviceFilesystemDispatcher.for_bot(...)``. The
dispatcher routes by the **real** ``DeviceAccessor`` boundary — the local
plugin (``LocalDeviceAccessor``, MockSeam). We drive that boundary's
``get_connection_info`` to report a ``local`` provider so ``for_bot``
returns the real ``LocalDeviceFileSystem``, which performs the actual
filesystem write/delete. The assertion then reads the boundary effect off
disk (same path + bytes the Arca push would carry).

Coverage of the call chain:
- upload: happy (write lands the exact bytes at the workspace path) + error.
- mkdir:  happy (writes an empty ``.keep`` at the dir) + error.
- delete: happy (removes exactly that path) + error (readonly → 403, no op).
"""
from __future__ import annotations

import shutil

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.resources.dependencies.resource import get_bot_workspace_dir
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_arca_device
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test
from tests.community.framework.device_seams import route_device_accessor_to_local


_OWNER = "u_owner"
_DEVICE_ID = "arca_dev_test"
_ENGINE = "openclaw"


def _route_for_bot_to_local(world) -> None:
    """Drive the real ``DeviceAccessor`` boundary so the dispatcher's
    ``for_bot`` returns the real ``LocalDeviceFileSystem``.

    ``DeviceFilesystemDispatcher.for_bot`` routes on
    ``device_plugin.get_connection_info(...)["device_provider"]``. Reporting
    ``"local"`` selects the local-fs impl (the boundary we can run in
    tests), which performs the real write/delete we then assert off disk.
    """
    # LocalDeviceAccessor is a plain core class now (not a MockSeam); drive its
    # boundary via the framework seam helper (guard-compliant — no mock-on-
    # world.get assignment inside tests/endpoints/).
    route_device_accessor_to_local(world)


def _workspace_dir(world, bot_id: str):
    return get_bot_workspace_dir(
        world.get(WorkspacePathFactory), _OWNER, bot_id, _ENGINE, "staff"
    )


def _seed_arca_bot(world, *, bot_id: str):
    """staff user → Arca-bound bot (binding_id joins the arca binding so the
    DeviceContextResolver finds the active binding and takes the arca branch);
    plus a freshly-cleared workspace dir so disk assertions are isolated.
    Returns the workspace dir.
    """
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
            "entity_type": "user",
            "creator_id": _OWNER,
            "active_engine": _ENGINE,
            "device_id": _DEVICE_ID,
            # DeviceContextResolver JOINs ac_bots.binding_id == ac_entity_device_binding.id
            "binding_id": binding_id,
        }
    )
    _route_for_bot_to_local(world)
    ws = _workspace_dir(world, bot_id)
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ============================================================
# upload  (POST /api/resources/files/upload)
# ============================================================

_CSV = b"col1,col2\n1,2\n"


def _seed_upload_happy(world) -> None:
    _seed_arca_bot(world, bot_id="bot_res_up")


def _assert_uploaded_bytes(response, world) -> None:
    body = response.json()
    assert body.get("success") is True, body
    # The write is delivered through the device-fs boundary (LocalDeviceFileSystem
    # here) — the bytes land at the workspace-relative path.
    landed = _workspace_dir(world, "bot_res_up") / "report.csv"
    assert landed.read_bytes() == _CSV, landed


@endpoint_test(
    method="POST",
    path="/api/resources/files/upload",
    scenario="happy_arca_upload_writes_exact_bytes",
    input=CaseInput(
        query_params={"bot_id": "bot_res_up"},
        headers={"x-user-id": _OWNER},
        files=[("files", ("report.csv", _CSV))],
    ),
    seed=_seed_upload_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_uploaded_bytes,),
)
def upload_arca_writes_exact_bytes():
    """Uploading a resource file pushes the exact bytes to the device path."""


@endpoint_test(
    method="POST",
    path="/api/resources/files/upload",
    scenario="error_no_files_422",
    input=CaseInput(
        query_params={"bot_id": "bot_res_up_err"},
        headers={"x-user-id": _OWNER},
        # No multipart ``files`` part → FastAPI rejects the required File(...).
        form_data={"_": "x"},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_res_up_err"),
    expect=ExpectError(status=422),
)
def upload_missing_files_returns_422():
    """Upload with no file part → 422 validation error, no device write."""


# ============================================================
# mkdir  (POST /api/resources/files/mkdir)
# ============================================================

def _assert_keep_written(response, world) -> None:
    body = response.json()
    assert body.get("success") is True, body
    keep = _workspace_dir(world, "bot_res_mkdir") / "newdir" / ".keep"
    assert keep.exists(), keep
    assert keep.read_bytes() == b"", keep


@endpoint_test(
    method="POST",
    path="/api/resources/files/mkdir",
    scenario="happy_arca_mkdir_writes_keep",
    input=CaseInput(
        query_params={"bot_id": "bot_res_mkdir"},
        headers={"x-user-id": _OWNER},
        form_data={"path": "newdir"},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_res_mkdir"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_keep_written,),
)
def mkdir_arca_writes_keep_file():
    """mkdir on an Arca bot writes an empty .keep to materialize the dir."""


@endpoint_test(
    method="POST",
    path="/api/resources/files/mkdir",
    scenario="error_missing_path_form_422",
    input=CaseInput(
        query_params={"bot_id": "bot_res_mkdir_err"},
        headers={"x-user-id": _OWNER},
        # No ``path`` form field → FastAPI rejects the required Form(...).
        form_data={"_": "x"},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_res_mkdir_err"),
    expect=ExpectError(status=422),
)
def mkdir_missing_path_returns_422():
    """mkdir with no path form field → 422, no device write."""


# ============================================================
# delete  (DELETE /api/resources/files)
# ============================================================

def _seed_delete_happy(world) -> None:
    ws = _seed_arca_bot(world, bot_id="bot_res_del")
    target = ws / "data" / "report.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_CSV)
    world._del_target = target


def _assert_deleted(response, world) -> None:
    assert response.json().get("success") is True, response.json()
    assert not world._del_target.exists(), world._del_target


@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="happy_arca_delete_removes_path",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": "bot_res_del"},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_delete_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_deleted,),
)
def delete_file_arca_removes_exact_path():
    """Deleting a resource file on an Arca bot removes exactly that path."""


@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="error_readonly_file_403",
    input=CaseInput(
        # AGENTS.md at workspace root is a hidden/readonly identity file →
        # _is_readonly → 403 before any device resolution.
        query_params={"path": "AGENTS.md", "bot_id": "bot_res_del_ro"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_arca_bot(world, bot_id="bot_res_del_ro"),
    expect=ExpectError(status=403),
)
def delete_file_readonly_returns_403():
    """Deleting a readonly identity file → 403, no device delete."""
