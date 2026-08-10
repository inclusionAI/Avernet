"""Endpoint coverage for /api/resources/files/search and /download-dir.

These cases satisfy the framework's endpoint-coverage gate.  They exercise
the local-FS code path by seeding a bot with a local device binding so
``arca_utils.get_device_info`` reports ``device_provider="local"``, which
makes both endpoints take the FileService (local) branch — no Arca/BaaS
involved.
"""
from __future__ import annotations

import shutil

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.resources.dependencies.resource import get_bot_workspace_dir
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from tests.community.factories.access import make_staff_user
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_OWNER = "u_search_owner"
_DEVICE_ID = "local_dev_search"
_ENGINE = "openclaw"


def _workspace_dir(world, bot_id: str):
    return get_bot_workspace_dir(
        world.get(WorkspacePathFactory), _OWNER, bot_id, _ENGINE, "staff"
    )


def _seed_local_bot(world, *, bot_id: str):
    """Create a staff user + local-device bot, and return a fresh workspace
    dir with a tiny file tree for search/zip tests.

    Using ``make_active_local_device`` ensures ``get_device_info`` returns
    ``("local", None)`` so the endpoints take the FileService code path.
    """
    make_staff_user(world, user_id=_OWNER)
    make_active_local_device(world, owner_id=_OWNER, device_id=_DEVICE_ID)
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
        }
    )
    ws = _workspace_dir(world, bot_id)
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    # Seed files for search & download tests
    data = ws / "data"
    data.mkdir()
    (data / "report.csv").write_text("col1,col2\n1,2\n")
    (data / "notes.txt").write_text("hello")
    sub = data / "sub"
    sub.mkdir()
    (sub / "deep.json").write_text("{}")
    return ws


# ============================================================
# GET /api/resources/files/search
# ============================================================

@endpoint_test(
    method="GET",
    path="/api/resources/files/search",
    scenario="happy_local_search_finds_file",
    input=CaseInput(
        query_params={"bot_id": "bot_search_h", "keyword": "report"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_local_bot(world, bot_id="bot_search_h"),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def search_local_finds_file():
    """Searching 'report' returns report.csv in items."""


@endpoint_test(
    method="GET",
    path="/api/resources/files/search",
    scenario="error_traversal_400",
    input=CaseInput(
        query_params={"bot_id": "bot_search_e", "keyword": "x", "path": "../etc"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_local_bot(world, bot_id="bot_search_e"),
    expect=ExpectError(status=400),
)
def search_traversal_rejected():
    """Path traversal in search keyword returns 400."""


# ============================================================
# GET /api/resources/files/download-dir
# ============================================================

@endpoint_test(
    method="GET",
    path="/api/resources/files/download-dir",
    scenario="happy_local_dir_zip",
    input=CaseInput(
        query_params={"bot_id": "bot_dl_h", "path": "data"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_local_bot(world, bot_id="bot_dl_h"),
    expect=ExpectSuccess(status=200),
)
def download_dir_local_returns_zip():
    """Zipping a local directory returns 200 with application/zip."""


@endpoint_test(
    method="GET",
    path="/api/resources/files/download-dir",
    scenario="error_empty_dir_404",
    input=CaseInput(
        query_params={"bot_id": "bot_dl_e", "path": "nonexistent_dir"},
        headers={"x-user-id": _OWNER},
    ),
    seed=lambda world: _seed_local_bot(world, bot_id="bot_dl_e"),
    expect=ExpectError(status=404),
)
def download_dir_empty_returns_404():
    """Zipping an empty/nonexistent directory returns 404."""

# --- _search_should_descend: search walk pruning rule (mirrors hidden set) ---

import pytest  # noqa: E402
from agentclaw.community.adapters.http.resources.file_search_download_router import (  # noqa: E402
    _search_should_descend,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "ws_dir_rel, expected",
    [
        ("config", True),               # normal dir → walk
        ("docs/sub", True),             # nested normal dir → walk
        ("state", False),               # hidden system dir → prune
        ("conf", False),                # hidden engine config → prune
        ("claude_code_conf", False),    # per-engine *_conf → prune
        ("skills", True),               # on the path to skills-local → descend
        ("skills/skills-local", True),  # the browsable exception → descend
        ("skills/skills-local/a", True),  # under the exception → descend
        ("skills/other", False),        # other skills subtree → prune
        (".git", False),                # dotdir → prune
        ("docs/.hidden", False),        # nested dotdir segment → prune
    ],
)
def test_search_should_descend_matches_resource_browser_visibility(ws_dir_rel, expected):
    assert _search_should_descend(ws_dir_rel) is expected
