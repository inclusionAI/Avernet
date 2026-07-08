"""End-to-end read-path tests for the resources file routes on a local bot.

Covers the thin ``file_router`` → ``ResourceFileService`` delegation for a local
(pathlib) bot: directory listing (with hidden-file filtering + skills-local
injection), preview, download, delete, and mkdir — plus the per-provider
``absolute_path`` presentation (host workspace path for local). teclaw/arca write
paths are covered in test_resources_teclaw_writes.py; teclaw reads in
test_resources_teclaw_reads.py.

Seeding mirrors the identity claude_md endpoint tests: a real local binding routes
the device-fs dispatch onto LocalDeviceFileSystem (pathlib mode) over the bot's
workspace dir on disk.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_arca_device
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_OWNER = "u_resfile_local"
_BOT = "bot_resfile_local"
_CSV = b"col1,col2\n1,2\n"


def _ws(world):
    return world.get(WorkspacePathFactory).get_bot_workspace_dir(
        _OWNER, _BOT, "openclaw", "staff"
    )


def _seed_local_bot(world) -> None:
    """Local (pathlib) bot with a seeded workspace tree on disk."""
    make_staff_user(world, user_id=_OWNER)
    binding_id = make_active_arca_device(world, owner_id=_OWNER, device_id=f"dev_{_BOT}")
    world.get(BotRepository).insert({
        "bot_id": _BOT, "bot_name": "Bot local", "owner_id": _OWNER,
        "owner_name": _OWNER, "bot_type": "service", "status": "ACTIVE",
        "entity_id": _OWNER, "entity_type": "staff", "creator_id": _OWNER,
        "active_engine": "openclaw", "device_id": f"dev_{_BOT}",
        "binding_id": binding_id,
    })
    ws = _ws(world)
    (ws / "data").mkdir(parents=True, exist_ok=True)
    (ws / "data" / "report.csv").write_bytes(_CSV)
    (ws / "AGENTS.md").write_text("identity", encoding="utf-8")   # hidden basename at root
    (ws / ".hidden").write_text("x", encoding="utf-8")            # dotfile
    # skills-local lives under the hidden "skills" dir → must be injected at root
    (ws / "skills" / "skills-local").mkdir(parents=True, exist_ok=True)


# ── list (root): filtering + skills-local injection + absolute_path ───────────


def _assert_root_listing(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    by_path = {i["path"]: i for i in body["items"]}
    # hidden basename + dotfile filtered; real dir kept; skills-local injected
    assert "AGENTS.md" not in by_path
    assert ".hidden" not in by_path
    assert "data" in by_path
    assert "skills/skills-local" in by_path
    # absolute_path is the device's own absolute path — for a local bot that's the
    # host workspace path (the pathlib entry's ``path``).
    assert by_path["data"]["absolute_path"] == f"{_ws(world)}/data"


@endpoint_test(
    method="GET",
    path="/api/resources/files",
    scenario="happy_local_list_root",
    input=CaseInput(
        query_params={"path": "", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_root_listing,),
)
def local_list_root():
    """Root listing filters hidden files, keeps real dirs, injects skills-local."""


# ── preview ──────────────────────────────────────────────────────────────────


def _assert_preview(response, world) -> None:
    body = response.json()
    assert body["success"] is True
    assert body["data"]["content"] == _CSV.decode("utf-8")
    assert body["data"]["size"] == len(_CSV)


@endpoint_test(
    method="GET",
    path="/api/resources/files/preview",
    scenario="happy_local_preview",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_preview,),
)
def local_preview():
    """Preview returns the file content for a local bot."""


# ── download ─────────────────────────────────────────────────────────────────


def _assert_download(response, world) -> None:
    assert response.content == _CSV


@endpoint_test(
    method="GET",
    path="/api/resources/files/download",
    scenario="happy_local_download",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_download,),
)
def local_download():
    """Download streams the file bytes for a local bot."""


# ── download missing file → 404 ──────────────────────────────────────────────


@endpoint_test(
    method="GET",
    path="/api/resources/files/download",
    scenario="error_local_download_missing",
    input=CaseInput(
        query_params={"path": "data/nope.csv", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectError(status=404),
)
def local_download_missing():
    """Downloading a missing file returns 404."""


# ── delete read-only identity file → 403 (guard preserved) ───────────────────


@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="error_local_delete_readonly",
    input=CaseInput(
        query_params={"path": "AGENTS.md", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectError(status=403),
)
def local_delete_readonly():
    """Deleting a root identity .md file is rejected (403)."""


# ── delete a real file → 200, gone from disk ─────────────────────────────────


def _assert_deleted(response, world) -> None:
    assert response.json()["success"] is True
    assert not (_ws(world) / "data" / "report.csv").exists()


@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="happy_local_delete",
    input=CaseInput(
        query_params={"path": "data/report.csv", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_deleted,),
)
def local_delete():
    """Deleting a real file removes it from disk."""


# ── delete a DIRECTORY → 200, recursively gone (regression: was delete_tree) ──


def _assert_dir_deleted(response, world) -> None:
    assert response.json()["success"] is True
    # the whole subtree is gone, not just an unlink that fails on a dir
    assert not (_ws(world) / "data").exists()


@endpoint_test(
    method="DELETE",
    path="/api/resources/files",
    scenario="happy_local_delete_directory",
    input=CaseInput(
        query_params={"path": "data", "bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_dir_deleted,),
)
def local_delete_directory():
    """Deleting a non-empty directory on a local bot removes it recursively."""


# ── mkdir → 200, .keep created ───────────────────────────────────────────────


def _assert_mkdir(response, world) -> None:
    assert response.json()["success"] is True
    assert (_ws(world) / "newdir" / ".keep").exists()


@endpoint_test(
    method="POST",
    path="/api/resources/files/mkdir",
    scenario="happy_local_mkdir",
    input=CaseInput(
        query_params={"bot_id": _BOT, "owner_id": _OWNER},
        headers={"x-user-id": _OWNER},
        form_data={"path": "newdir"},
    ),
    seed=_seed_local_bot,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_mkdir,),
)
def local_mkdir():
    """mkdir creates the directory via a .keep placeholder on disk."""
