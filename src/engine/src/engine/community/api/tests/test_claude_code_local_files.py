"""Real HTTP -> Claude Code adapter -> local port file contract."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.file.router import router
from engine.community.engines.claude_code.engine import ClaudeCodeCommunityEngine
from engine.community.manager import EngineManager


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_DEFAULT_CWD", str(tmp_path))
    engine = ClaudeCodeCommunityEngine()
    manager = EngineManager("claude_code")
    manager._active_engine = engine
    monkeypatch.setattr(EngineManager, "_instance", manager)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_first_upload_and_binary_readback_without_relay(client, tmp_path):
    target = tmp_path / "skills-local" / "sample" / "assets" / "image.bin"
    content = b"\x00\xff\x80sample\x00"
    absent = client.post("/api/file/list", json={"dir_path": str(target.parent)})
    assert absent.status_code == 404
    uploaded = client.post(
        "/api/file/upload",
        data={"target_path": str(target)},
        files={"file": ("image.bin", content)},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert target.read_bytes() == content
    response = client.post("/api/file/read", json={"file_path": str(target)})
    assert response.status_code == 200
    assert response.content == content


def test_recursive_listing_preserves_nested_backup_files(client, tmp_path):
    root = tmp_path / "skill"
    for relative in ["SKILL.md", "scripts/nested/run.py", "assets/image.bin"]:
        response = client.post(
            "/api/file/upload",
            data={"target_path": str(root / relative)},
            files={"file": (relative, b"content")},
        )
        assert response.status_code == 200
    listing = client.post(
        "/api/file/list", json={"dir_path": str(root), "recursive": True}
    )
    files = listing.json()["data"]["files"]
    assert {entry["relative_path"] for entry in files if not entry["is_dir"]} == {
        "SKILL.md",
        "scripts/nested/run.py",
        "assets/image.bin",
    }
    shallow = client.post("/api/file/list", json={"dir_path": str(root)})
    assert {entry["relative_path"] for entry in shallow.json()["data"]["files"]} == {
        "SKILL.md",
        "scripts",
        "assets",
    }


@pytest.mark.parametrize("endpoint", ["remove", "rmtree"])
def test_delete_symlink_preserves_external_target(client, tmp_path, endpoint):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "keep").write_bytes(b"keep")
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    response = client.post("/api/file/" + endpoint, json={"target_path": str(link)})
    assert response.status_code == 200
    assert not link.is_symlink()
    assert (outside / "keep").read_bytes() == b"keep"


def test_remove_directory_and_single_file_rmtree(client, tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "data").write_bytes(b"data")
    response = client.post("/api/file/remove", json={"target_path": str(directory)})
    assert response.status_code == 200
    assert response.json()["data"]["path_type"] == "directory"
    assert not directory.exists()
    file = tmp_path / "file"
    file.write_bytes(b"content")
    assert (
        client.post("/api/file/rmtree", json={"target_path": str(file)}).status_code
        == 200
    )
    assert not file.exists()
    assert (
        client.post("/api/file/rmtree", json={"target_path": str(file)}).status_code
        == 404
    )


def test_out_of_root_and_symlink_escape_fail_without_writes(client, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    for target in [outside / "data", link / "data", tmp_path / ".." / "data"]:
        response = client.post(
            "/api/file/upload",
            data={"target_path": str(target)},
            files={"file": ("data", b"must not write")},
        )
        assert response.status_code in {400, 403}
    assert not (outside / "data").exists()
    assert (
        client.post("/api/file/rmtree", json={"target_path": str(tmp_path)}).status_code
        == 403
    )


def test_file_type_and_overwrite_errors(client, tmp_path):
    directory = tmp_path / "dir"
    directory.mkdir()
    assert (
        client.post(
            "/api/file/upload",
            data={"target_path": str(directory)},
            files={"file": ("dir", b"x")},
        ).status_code
        == 409
    )
    target = tmp_path / "file"
    target.write_bytes(b"old")
    response = client.post(
        "/api/file/upload",
        data={"target_path": str(target)},
        files={"file": ("file", b"new")},
    )
    assert response.json()["data"]["overwritten"] is True
    assert target.read_bytes() == b"new"
    assert (
        client.post("/api/file/list", json={"dir_path": str(target)}).status_code == 400
    )
    assert (
        client.post("/api/file/read", json={"file_path": str(directory)}).status_code
        == 404
    )


def test_workspace_namespace_uses_configured_cwd(client, tmp_path):
    response = client.post(
        "/api/file/upload",
        data={"target_path": "workspace/project/file.txt"},
        files={"file": ("file.txt", b"project")},
    )
    assert response.status_code == 200
    assert (tmp_path / "project" / "file.txt").read_bytes() == b"project"
