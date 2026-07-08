"""Unit tests for the OpenClaw skills ACL adapter.

Drives `OpenClawSkillsAdapter` against a fake `OpenClawSkillsPort` returning
canned dicts: the adapter serializes request DTOs → param dicts, calls the port,
and builds result DTOs. The 10 per-skill ops are not OpenClaw capabilities — the
adapter raises `CapabilityNotSupportedError` (no port call).
"""
from __future__ import annotations

import pytest

from engine.community.core.adapters.openclaw.skills import OpenClawSkillsAdapter
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.skills.models import (
    CenterEnsureItem,
    CenterEnsureRequest,
    CleanSymlinksRequest,
    SkillConfig,
    SkillExecutionRequest,
    SymlinkItem,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
)


class _FakeSkillsPort:
    def __init__(self, **results):
        self._results = results
        self.calls: dict[str, dict] = {}

    async def ensure_center_skills(self, params):
        self.calls["ensure_center_skills"] = params
        return self._results.get("ensure_center_skills", {"ok": [], "failed": []})

    async def sync_symlinks(self, params):
        self.calls["sync_symlinks"] = params
        return self._results["sync_symlinks"]

    async def sync_bindpaths(self, params):
        self.calls["sync_bindpaths"] = params
        return self._results["sync_bindpaths"]

    async def clean_symlinks(self, params):
        self.calls["clean_symlinks"] = params
        return self._results["clean_symlinks"]


async def test_ensure_center_skills_serializes_and_builds_result():
    port = _FakeSkillsPort(ensure_center_skills={
        "ok": [{"skill_uuid": "u1", "version": "1"}],
        "failed": [{"skill_uuid": "u2", "version": "2", "reason": "NAS missing"}],
    })
    adapter = OpenClawSkillsAdapter(port)
    req = CenterEnsureRequest(items=[
        CenterEnsureItem(skill_uuid="u1", version="1"),
        CenterEnsureItem(skill_uuid="u2", version="2"),
    ])
    result = await adapter.ensure_center_skills(req)
    # serialized to primitive items
    assert port.calls["ensure_center_skills"]["items"] == [
        {"skill_uuid": "u1", "version": "1"},
        {"skill_uuid": "u2", "version": "2"},
    ]
    # built DTOs
    assert [(i.skill_uuid, i.version) for i in result.ok] == [("u1", "1")]
    assert result.failed[0].reason == "NAS missing"


async def test_sync_symlinks_serializes_and_builds_result():
    port = _FakeSkillsPort(sync_symlinks={
        "total": 2, "created": ["a"], "updated": ["b"], "kept": [], "removed": ["c"],
        "base_dir": "/home/admin/.extra-skills",
    })
    adapter = OpenClawSkillsAdapter(port)
    req = SyncSymlinksRequest(symlinks=[
        SymlinkItem(source="s/a", target="a"),
        SymlinkItem(source="s/b", target="b"),
    ])
    result = await adapter.sync_symlinks(req)
    assert port.calls["sync_symlinks"]["symlinks"] == [
        {"source": "s/a", "target": "a"}, {"source": "s/b", "target": "b"},
    ]
    assert result.total == 2
    assert result.created == ["a"]
    assert result.removed == ["c"]
    assert result.base_dir == "/home/admin/.extra-skills"


async def test_sync_bindpaths_forwards_clean_target_dir():
    port = _FakeSkillsPort(sync_bindpaths={
        "total": 1, "created": ["/x"], "updated": [], "kept": [], "removed": [],
    })
    adapter = OpenClawSkillsAdapter(port)
    req = SyncBindPathsRequest(
        symlinks=[SymlinkItem(source="/src", target="/x")], clean_target_dir=False,
    )
    result = await adapter.sync_bindpaths(req)
    assert port.calls["sync_bindpaths"]["clean_target_dir"] is False
    assert result.total == 1
    assert result.created == ["/x"]


async def test_clean_symlinks_serializes_and_builds_result():
    port = _FakeSkillsPort(clean_symlinks={
        "directories_scanned": 2, "removed": ["/d/x", "/d/y"],
    })
    adapter = OpenClawSkillsAdapter(port)
    result = await adapter.clean_symlinks(CleanSymlinksRequest(directories=["/d", "/e"]))
    assert port.calls["clean_symlinks"]["directories"] == ["/d", "/e"]
    assert result.directories_scanned == 2
    assert result.removed == ["/d/x", "/d/y"]


@pytest.mark.parametrize("call", [
    lambda a: a.list_skills(),
    lambda a: a.get_skill("s1"),
    lambda a: a.install_skill(SkillConfig(skill_id="s1")),
    lambda a: a.uninstall_skill("s1"),
    lambda a: a.update_skill("s1", SkillConfig(skill_id="s1")),
    lambda a: a.enable_skill("s1"),
    lambda a: a.disable_skill("s1"),
    lambda a: a.execute_skill(SkillExecutionRequest(skill_id="s1", action="run")),
    lambda a: a.validate_skill("s1"),
    lambda a: a.discover_skills("src"),
])
async def test_per_skill_ops_raise_capability_not_supported(call):
    adapter = OpenClawSkillsAdapter(_FakeSkillsPort())
    with pytest.raises(CapabilityNotSupportedError):
        await call(adapter)
