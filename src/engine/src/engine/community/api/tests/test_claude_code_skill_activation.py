"""HTTP regression: uploaded skills must become readable active links without Relay."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.skills.router import router
from engine.community.engines.claude_code.engine import ClaudeCodeCommunityEngine
from engine.community.manager import EngineManager
from engine.community.plugins.claude_code._base import ClaudeCodePortBase


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    async def no_relay(self):
        raise AssertionError("Skill filesystem activation must not call Relay")
    monkeypatch.setattr(ClaudeCodePortBase, "_relay", no_relay)
    manager = EngineManager("claude_code")
    manager._active_engine = ClaudeCodeCommunityEngine()
    monkeypatch.setattr(EngineManager, "_instance", manager)
    app = FastAPI()
    app.include_router(router)
    source = tmp_path / ".claude_code/workspace/skills/skills-local/retro"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: retro\n---\nRetrospective")
    target = tmp_path / ".claude/skills/retro"
    with TestClient(app) as client:
        yield client, source, target


def bind(client, source, target, clean=True):
    return client.post("/api/skills/symlink/bindpath", json={
        "symlinks": [{"source": str(source), "target": str(target)}],
        "clean_target_dir": clean,
    })


def test_first_activation_and_idempotent_retry(runtime):
    client, source, target = runtime
    assert not target.parent.exists()
    response = bind(client, source, target)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["created"] == [str(target)]
    assert target.is_symlink()
    assert target.resolve() == source
    assert (target / "SKILL.md").read_text() == (source / "SKILL.md").read_text()
    assert bind(client, source, target).json()["data"]["kept"] == [str(target)]


def test_cleanup_option_and_disable_preserve_source(runtime):
    client, source, target = runtime
    target.parent.mkdir(parents=True)
    old = target.parent / "old"
    old.symlink_to(source)
    ordinary = target.parent / "user-file"
    ordinary.write_text("keep")
    assert bind(client, source, target, clean=False).status_code == 200
    assert old.is_symlink()
    assert bind(client, source, target).json()["data"]["removed"] == [str(old)]
    response = client.post("/api/skills/symlink/clean", json={"directories": [str(target.parent)]})
    assert response.status_code == 200
    assert not target.is_symlink()
    assert (source / "SKILL.md").exists()
    assert ordinary.read_text() == "keep"


def test_invalid_source_does_not_create_or_remove_links(runtime):
    client, source, target = runtime
    target.parent.mkdir(parents=True)
    old = target.parent / "old"
    old.symlink_to(source)
    response = bind(client, source / "absent", target)
    assert response.status_code == 409, response.text
    assert old.is_symlink()
    assert not target.exists()


def test_occupied_target_is_not_overwritten(runtime):
    client, source, target = runtime
    target.mkdir(parents=True)
    (target / "keep").write_text("keep")
    assert bind(client, source, target).status_code == 409
    assert (target / "keep").read_text() == "keep"


def test_outside_root_rejected(runtime, tmp_path):
    client, source, target = runtime
    outside = tmp_path.parent / "unowned-skill"
    assert bind(client, source, outside).status_code == 400
    assert not outside.exists()


def test_replacement_failure_preserves_previous_mapping(runtime, monkeypatch):
    client, source, target = runtime
    target.parent.mkdir(parents=True)
    old_source = source.parent / "previous"
    old_source.mkdir()
    target.symlink_to(old_source)
    def fail(*args, **kwargs):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "symlink_to", fail)
    with pytest.raises(PermissionError):
        bind(client, source, target)
    assert target.resolve() == old_source


def test_relative_sync_and_empty_cleanup(runtime):
    client, source, target = runtime
    nested = target.parent / "sources/retro"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("retro")
    response = client.post("/api/skills/symlink", json={"symlinks": [
        {"source": "sources/retro", "target": "nested/retro"}
    ]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["created"] == ["nested/retro"]
    link = target.parent / "nested/retro"
    assert link.resolve() == nested
    assert client.post("/api/skills/symlink", json={"symlinks": []}).json()["data"]["removed"] == ["nested/retro"]
    assert (nested / "SKILL.md").exists()


def test_batch_parent_conflict_has_no_partial_changes(runtime):
    client, source, target = runtime
    target.parent.mkdir(parents=True)
    blocked = target.parent / "blocked"
    blocked.write_text("keep")
    response = client.post("/api/skills/symlink/bindpath", json={"symlinks": [
        {"source": str(source), "target": str(target)},
        {"source": str(source), "target": str(blocked / "second")},
    ]})
    assert response.status_code == 409
    assert not target.is_symlink()
    assert blocked.read_text() == "keep"


def test_unreadable_source_has_no_partial_changes(runtime, monkeypatch):
    client, source, target = runtime
    original = Path.open
    def denied(path, *args, **kwargs):
        if path == source / "SKILL.md":
            raise PermissionError("unreadable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    assert bind(client, source, target).status_code == 409
    assert not target.parent.exists()


@pytest.mark.parametrize("bad", ["relative", "", "/tmp/../escape"])
def test_invalid_absolute_paths(runtime, bad):
    client, source, target = runtime
    assert bind(client, source, bad).status_code == 400
    assert not target.parent.exists()


def test_duplicate_root_and_self_targets(runtime):
    client, source, target = runtime
    for bad in (source, target.parent):
        assert bind(client, source, bad).status_code == 400
    response = client.post("/api/skills/symlink/bindpath", json={"symlinks": [
        {"source": str(source), "target": str(target)},
        {"source": str(source), "target": str(target)},
    ]})
    assert response.status_code == 400
    assert not target.exists()


def test_replace_existing_mapping_and_cleanup_paths(runtime):
    client, source, target = runtime
    target.parent.mkdir(parents=True)
    target.symlink_to(source.parent / "missing")
    assert bind(client, source, target).json()["data"]["updated"] == [str(target)]
    assert target.resolve() == source
    assert client.post("/api/skills/symlink/clean", json={"directories": [str(source / "absent")]}).json()["data"]["directories_scanned"] == 0
    assert client.post("/api/skills/symlink/clean", json={"directories": [str(source / "SKILL.md")]}).status_code == 400
    assert client.post("/api/skills/symlink", json={"symlinks": [{"source": "../escape", "target": "retro"}]}).status_code == 400


@pytest.mark.parametrize("existing", [False, True])
def test_nested_batch_targets_do_not_write_into_uploaded_source(runtime, existing):
    client, source, target = runtime
    if existing:
        target.parent.mkdir(parents=True)
        target.symlink_to(source)
    response = client.post("/api/skills/symlink/bindpath", json={"symlinks": [
        {"source": str(source), "target": str(target)},
        {"source": str(source), "target": str(target / "nested")},
    ]})
    assert response.status_code == 400
    assert target.exists() is existing
    assert not (source / "nested").exists()


def test_relative_reconcile_preserves_all_skill_package_symlinks(runtime):
    client, source, target = runtime
    base = target.parent
    internal = []
    for name in ("selected", "unselected"):
        package = base / "sources" / name
        (package / "assets").mkdir(parents=True)
        (package / "SKILL.md").write_text(name)
        (package / "assets/data").write_text("keep")
        link = package / "assets/shared"
        link.symlink_to("data")
        internal.append(link)
    active = base / "active"
    active.mkdir()
    old = active / "old"
    old.symlink_to(base / "sources/unselected")
    response = client.post("/api/skills/symlink", json={"symlinks": [
        {"source": "sources/selected", "target": "active/selected"}
    ]})
    assert response.status_code == 200, response.text
    assert not old.is_symlink()
    assert all(link.is_symlink() and link.read_text() == "keep" for link in internal)
    cleared = client.post("/api/skills/symlink", json={"symlinks": []})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["removed"] == ["active/selected"]
    assert all(link.is_symlink() and link.read_text() == "keep" for link in internal)
