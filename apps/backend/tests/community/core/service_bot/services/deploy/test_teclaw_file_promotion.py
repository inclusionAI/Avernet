"""Unit tests for TeclawFilePromotion (gather engine files → OSS → refs)."""

from unittest.mock import MagicMock
from typing import Any, Optional

from agentclaw.community.core.apply_engine.cli_tools.store import (
    CliToolStore,
)
from tests.community.core.apply_engine.cli_tools._fakes import (
    FakeCliToolRepo,
)

import pytest

from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
    PromotedRefs,
    TeclawFilePromotion,
    TeclawFilePromotionError,
)

pytestmark = pytest.mark.unit

_HIDDEN_LOCAL_SKILL_DIRS = (
    ".teu14-a2a-protocol.apply-abcdef1234567890",
    ".teu14-a2a-protocol.rollback-fedcba0987654321",
)


class _FakeDeviceFs:
    """Stands in for the source bot's TeclawDeviceFileSystem.

    ``list_dir(ns)`` returns the seeded entries for that namespace; ``read_file``
    returns seeded bytes keyed by the logical path (``"workspace/sub/x"``).
    """

    def __init__(self, *, listings: dict, contents: dict):
        self._listings = listings
        self._contents = contents
        self.read_calls: list[str] = []

    async def list_dir(self, dir_path, *, recursive=False):
        return self._listings.get(dir_path, [])

    async def read_file(self, file_path):
        self.read_calls.append(file_path)
        return self._contents.get(file_path)


class _ConcurrentLocalSkillDeviceFs:
    """A Teclaw listing that survives a visible refresh during a retry loop.

    This is the smallest unit-level reproduction of the engine's actual rename
    exchange: the old package path is visible to listing, the first read races the
    publish, and the authoritative package is restored under the same logical
    path by a re-listing before the bounded retry completes.
    """

    def __init__(self, *, initial: list[dict[str, Any]], final: list[dict[str, Any]]):
        self._initial = initial
        self._final = final
        self._listing_index = 0
        self.listings: list[str] = []
        self.read_calls: list[str] = []
        self.read_results: list[Optional[bytes]] = []

    async def list_dir(
        self, dir_path: str, *, recursive: bool = False
    ) -> list[dict[str, Any]]:
        self.listings.append(dir_path)
        self._listing_index += 1
        return self._final if self._listing_index > 1 else self._initial

    async def read_file(self, file_path: str) -> bytes | None:
        self.read_calls.append(file_path)
        if self._listing_index == 1:
            self.read_results.append(None)
            return None
        self.read_results.append(b"new")
        return b"new"


def _oss_ok():
    oss = MagicMock()
    oss.put_object.return_value = True
    return oss


def _promo(oss):
    # W9 collaborators are required, and a bot with no tools is the empty
    # table rather than an unwired promotion — which is what these cases are.
    return TeclawFilePromotion(
        oss_storage=oss,
        cli_tool_repository=FakeCliToolRepo(),
        cli_tool_store=CliToolStore(object_storage=oss, store_base=lambda: "base"),
    )


_ARGS = dict(
    env="pre",
    entity_type="staff",
    entity_id="100018",
    bot_id="20260614_5t0gnh75",
    publish_id=88,
    stage="verify",
)
# Stage-scoped OSS prefix the refs/keys must use.
_PREFIX = "staff_100018/20260614_5t0gnh75_88_verify/teclaw"


@pytest.mark.asyncio
async def test_stages_workspace_and_identity_to_oss_with_refs():
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/workspace/report.csv", "is_dir": False},
                {"path": "/workspace/sub", "is_dir": True},
                {"path": "/workspace/sub/a.md", "is_dir": False},
            ],
            "identity": [
                {"path": "/identity/MEMORY.md", "is_dir": False},
            ],
        },
        contents={
            "workspace/report.csv": b"csv",
            "workspace/sub/a.md": b"md",
            "identity/MEMORY.md": b"mem",
        },
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)

    assert isinstance(refs, PromotedRefs)
    # resources: workspace files (dir entry skipped), stage-scoped path + basename name
    assert refs.resources == [
        {
            "name": "report.csv",
            "store": "bot-data",
            "path": f"{_PREFIX}/workspace/report.csv",
        },
        {"name": "a.md", "store": "bot-data", "path": f"{_PREFIX}/workspace/sub/a.md"},
    ]
    assert refs.identity_files == [
        {
            "name": "MEMORY.md",
            "store": "bot-data",
            "path": f"{_PREFIX}/identity/MEMORY.md",
        },
    ]
    # OSS keys = store base + ref path
    put_keys = {c.args[0] for c in oss.put_object.call_args_list}
    assert put_keys == {
        f"teclaw/pre/bolt_data/{_PREFIX}/workspace/report.csv",
        f"teclaw/pre/bolt_data/{_PREFIX}/workspace/sub/a.md",
        f"teclaw/pre/bolt_data/{_PREFIX}/identity/MEMORY.md",
    }


@pytest.mark.asyncio
async def test_skills_local_files_are_swept_into_resources():
    """A user-uploaded local skill lives flat under /workspace/skills-local; the
    recursive workspace sweep stages its bytes to the stage-scoped OSS key. The
    new-stage container auto-discovers the skill from that materialized dir — no
    separate ``skills`` ref is composed (see spec 2026-06-16-teclaw-local-skill-upload)."""
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/workspace/skills-local", "is_dir": True},
                {"path": "/workspace/skills-local/my-skill", "is_dir": True},
                {"path": "/workspace/skills-local/my-skill/SKILL.md", "is_dir": False},
            ],
            "identity": [],
        },
        contents={"workspace/skills-local/my-skill/SKILL.md": b"skill"},
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert refs.resources == [
        {
            "name": "SKILL.md",
            "store": "bot-data",
            "path": f"{_PREFIX}/workspace/skills-local/my-skill/SKILL.md",
        },
    ]
    assert (
        f"teclaw/pre/bolt_data/{_PREFIX}/workspace/skills-local/my-skill/SKILL.md"
        in {c.args[0] for c in oss.put_object.call_args_list}
    )


@pytest.mark.asyncio
async def test_empty_namespaces_yield_no_refs():
    # Both namespaces genuinely empty ([] not None) → no refs, no OSS writes.
    fs = _FakeDeviceFs(listings={"workspace": [], "identity": []}, contents={})
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert refs.resources == [] and refs.identity_files == []
    oss.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_failed_listing_raises_not_silently_empty():
    # list_dir returns None on engine error — must hard-fail, not ship an
    # incomplete snapshot (there is no DB mirror to fall back on).
    fs = _FakeDeviceFs(listings={"workspace": None, "identity": []}, contents={})
    with pytest.raises(TeclawFilePromotionError):
        await _promo(_oss_ok()).stage_files(device_fs=fs, **_ARGS)


@pytest.mark.asyncio
async def test_out_of_namespace_path_skipped():
    # A stray cross-namespace entry in a workspace listing is skipped (not
    # garbled into a wrong key / wrong read).
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/identity/secret.md", "is_dir": False},  # stray
                {"path": "/workspace/ok.txt", "is_dir": False},
            ],
            "identity": [],
        },
        contents={"workspace/ok.txt": b"ok"},
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert [r["name"] for r in refs.resources] == ["ok.txt"]
    # the stray /identity path was not read
    assert "identity/secret.md" not in fs.read_calls


@pytest.mark.asyncio
async def test_node_modules_files_are_excluded_from_snapshot():
    # node_modules is regenerable dependency content, not a user file to carry
    # across stages: it is swept over but never read or staged, at any depth.
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/workspace/app.py", "is_dir": False},
                {"path": "/workspace/node_modules", "is_dir": True},
                {
                    "path": "/workspace/node_modules/rxjs/dist/esm/index.js",
                    "is_dir": False,
                },
                {"path": "/workspace/sub/node_modules/pkg/a.js", "is_dir": False},
            ],
            "identity": [],
        },
        contents={
            "workspace/app.py": b"code",
            # node_modules contents present but must not be read/staged
            "workspace/node_modules/rxjs/dist/esm/index.js": b"js",
            "workspace/sub/node_modules/pkg/a.js": b"js",
        },
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert [r["name"] for r in refs.resources] == ["app.py"]
    # node_modules files were never read nor written to OSS (any depth)
    assert fs.read_calls == ["workspace/app.py"]
    assert {c.args[0] for c in oss.put_object.call_args_list} == {
        f"teclaw/pre/bolt_data/{_PREFIX}/workspace/app.py",
    }


@pytest.mark.asyncio
async def test_unreadable_node_modules_map_does_not_fail_build():
    # Reproduces the production alert: a source-map under node_modules that
    # list_dir reports but read_file cannot read (dangling symlink → None). It
    # must be skipped as excluded content, not turned into a hard build failure.
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/workspace/report.csv", "is_dir": False},
                {
                    "path": "/workspace/node_modules/rxjs/dist/esm/internal/"
                    "operators/single.js.map",
                    "is_dir": False,
                },
            ],
            "identity": [],
        },
        contents={"workspace/report.csv": b"csv"},  # the .map has no content
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert [r["name"] for r in refs.resources] == ["report.csv"]
    assert (
        "workspace/node_modules/rxjs/dist/esm/internal/operators/single.js.map"
        not in fs.read_calls
    )


@pytest.mark.asyncio
async def test_unreadable_source_file_raises():
    fs = _FakeDeviceFs(
        listings={
            "workspace": [{"path": "/workspace/x", "is_dir": False}],
            "identity": [],
        },
        contents={},  # read_file returns None → hard error
    )
    with pytest.raises(TeclawFilePromotionError):
        await _promo(_oss_ok()).stage_files(device_fs=fs, **_ARGS)


@pytest.mark.asyncio
async def test_tclaw_skill_internal_staging_dirs_are_excluded_and_retired():
    """Hidden stage/rollback dirs are not snapshot content.

    The local-package route stages these directories under ``skills-local`` and
    removes them after a rename. A recursive listing can see them during that
    swap; reading one back may fail because the publisher has already moved it.
    Promotion should not treat this as a live source read.
    """
    fs = _FakeDeviceFs(
        listings={
            "workspace": [
                {"path": "/workspace/skills-local", "is_dir": True},
                {
                    "path": f"/workspace/skills-local/{_HIDDEN_LOCAL_SKILL_DIRS[0]}",
                    "is_dir": True,
                },
                {
                    "path": f"/workspace/skills-local/{_HIDDEN_LOCAL_SKILL_DIRS[0]}/SKILL.md",
                    "is_dir": False,
                },
                {
                    "path": f"/workspace/skills-local/{_HIDDEN_LOCAL_SKILL_DIRS[1]}",
                    "is_dir": True,
                },
                {
                    "path": f"/workspace/skills-local/{_HIDDEN_LOCAL_SKILL_DIRS[1]}/SKILL.md",
                    "is_dir": False,
                },
            ],
            "identity": [],
        },
        contents={},
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)

    assert refs.resources == []
    assert oss.put_object.call_count == 0
    for dir_name in _HIDDEN_LOCAL_SKILL_DIRS:
        assert f"workspace/skills-local/{dir_name}/SKILL.md" not in fs.read_calls


@pytest.mark.asyncio
async def test_local_skill_file_read_tolerates_publish_renaming():
    """A listed local-skill file is retried only until the package publisher settles.

    The engine's package replace moves the old directory to a rollback and moves
    the stage into the canonical path. A build can thus list the old path, call
    ``read_file`` during the swap, and get 404. It must re-list and use then-current
    bytes instead of failing; if the path is still not readable, the original
    hard-fail promotion contract remains.
    """
    fs = _ConcurrentLocalSkillDeviceFs(
        initial=[
            {"path": "/workspace/skills-local/teu14-a2a-protocol", "is_dir": True},
            {
                "path": "/workspace/skills-local/teu14-a2a-protocol/SKILL.md",
                "is_dir": False,
            },
        ],
        final=[
            {"path": "/workspace/skills-local/teu14-a2a-protocol", "is_dir": True},
            {
                "path": "/workspace/skills-local/teu14-a2a-protocol/SKILL.md",
                "is_dir": False,
            },
        ],
    )
    oss = _oss_ok()
    refs = await _promo(oss).stage_files(device_fs=fs, **_ARGS)
    assert refs.resources == [
        {
            "name": "SKILL.md",
            "store": "bot-data",
            "path": f"{_PREFIX}/workspace/skills-local/teu14-a2a-protocol/SKILL.md",
        },
    ]
    assert fs.read_results == [None, b"new"]


@pytest.mark.asyncio
async def test_unreadable_local_skill_file_still_fails_after_publish_settles():
    """The retry stays bounded: a genuinely missing file remains a hard failure."""
    fs = _ConcurrentLocalSkillDeviceFs(
        initial=[
            {"path": "/workspace/skills-local/teu14-a2a-protocol", "is_dir": True},
            {
                "path": "/workspace/skills-local/teu14-a2a-protocol/SKILL.md",
                "is_dir": False,
            },
        ],
        final=[],
    )
    with pytest.raises(TeclawFilePromotionError, match="source file unreadable"):
        await _promo(_oss_ok()).stage_files(device_fs=fs, **_ARGS)


@pytest.mark.asyncio
async def test_oss_put_failure_raises():
    fs = _FakeDeviceFs(
        listings={
            "workspace": [{"path": "/workspace/x", "is_dir": False}],
            "identity": [],
        },
        contents={"workspace/x": b"data"},
    )
    oss = MagicMock()
    oss.put_object.return_value = False
    with pytest.raises(TeclawFilePromotionError):
        await _promo(oss).stage_files(device_fs=fs, **_ARGS)


@pytest.mark.asyncio
async def test_reads_use_logical_namespace_relative_paths():
    fs = _FakeDeviceFs(
        listings={
            "workspace": [{"path": "/workspace/sub/a.md", "is_dir": False}],
            "identity": [],
        },
        contents={"workspace/sub/a.md": b"x"},
    )
    await _promo(_oss_ok()).stage_files(device_fs=fs, **_ARGS)
    # read_file is called with the logical (no leading slash) form so the teclaw
    # mapper maps it back to /workspace/... rather than mis-treating it as a host path.
    assert fs.read_calls == ["workspace/sub/a.md"]
