"""File count contract through real Engine composition and local subprocess I/O."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.file.router import router
from engine.community.core.file.protocol import FileService
from engine.community.engines.claude_code.engine import ClaudeCodeCommunityEngine
from engine.community.manager import EngineManager


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CODE_DEFAULT_CWD", raising=False)
    monkeypatch.delenv("RELAY_DEFAULT_CWD", raising=False)
    return home


def client_for(monkeypatch, *, legacy=False):
    engine = ClaudeCodeCommunityEngine()
    if legacy:
        class LegacyFileService(FileService):
            pass
        engine._file = LegacyFileService()
    manager = EngineManager("claude_code")
    manager._active_engine = engine
    monkeypatch.setattr(EngineManager, "_instance", manager)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_count_works_without_engine_file_implementation(world, monkeypatch, caplog, legacy):
    root = world / ".claude_code/workspace/config"
    (root / "nested").mkdir(parents=True)
    (root / "one").write_text("one")
    (root / "nested/two").write_text("two")
    with client_for(monkeypatch, legacy=legacy) as client, caplog.at_level("INFO"):
        response = client.get("/api/file/count", params={"path": "workspace/config", "request_id": "count-1"},
                              headers={"Authorization": "Bearer do-not-log-credential"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["file_count"] == 2
    assert response.json()["data"]["path"] == "workspace/config"
    events = [r for r in caplog.records if r.name == "api-file"]
    assert [r.msg for r in events] == ["engine.file_count.request", "engine.file_count.response"]
    assert all(r.fields["request_id"] == "count-1" for r in events)
    assert events[-1].fields["response"]["file_count"] == 2
    assert "do-not-log-credential" not in str([r.__dict__ for r in caplog.records])
    assert any(r.msg == "engine.file_count.cleanup" for r in caplog.records)


@pytest.mark.parametrize("setting", ["CLAUDE_CODE_DEFAULT_CWD", "RELAY_DEFAULT_CWD"])
def test_custom_workspace_does_not_reinterpret_relative_paths_or_allow_parent(world, monkeypatch, setting):
    workspace = world / "custom/project"
    (workspace / "config").mkdir(parents=True)
    (workspace / "config/one").write_text("one")
    (world / ".claude_code/workspace/config").mkdir(parents=True)
    monkeypatch.setenv(setting, str(workspace))
    with client_for(monkeypatch) as client:
        for path, count in [("workspace/config", 0), (str(workspace / "config"), 1)]:
            response = client.get("/api/file/count", params={"path": path, "request_id": "custom"})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["file_count"] == count
        response = client.get("/api/file/count", params={"path": str(workspace.parent), "request_id": "outside"})
        assert response.status_code == 403
        assert response.json()["detail"] == "path_forbidden"


@pytest.mark.parametrize("path,status,code", [
    ("workspace/missing", 404, "path_not_found"),
    ("workspace/file", 400, "not_directory"),
    ("../outside", 403, "path_forbidden"),
    ("", 400, "invalid_path"),
])
def test_stable_failures_are_logged_without_credentials(world, monkeypatch, caplog, path, status, code):
    root = world / ".claude_code/workspace"
    root.mkdir(parents=True)
    (root / "file").write_text("data")
    with client_for(monkeypatch) as client, caplog.at_level("INFO"):
        response = client.get("/api/file/count", params={"path": path, "request_id": "failure"},
                              headers={"Cookie": "session=do-not-log-cookie"})
    assert response.status_code == status, response.text
    assert response.json()["detail"] == code
    events = [r for r in caplog.records if r.name == "api-file"]
    assert [r.msg for r in events] == ["engine.file_count.request", "engine.file_count.failure"]
    assert events[-1].fields["error_code"] == code
    assert "do-not-log-cookie" not in str([r.__dict__ for r in caplog.records])


def test_links_follow_only_explicit_claude_roots(world, monkeypatch):
    root = world / ".claude_code/workspace"
    root.mkdir(parents=True)
    skills = world / ".claude/skills"
    skills.mkdir(parents=True)
    (skills / "skill.md").write_text("data")
    outside = world / "openclawExt"
    outside.mkdir()
    (outside / "secret").write_text("private")
    (root / "allowed").symlink_to(skills, target_is_directory=True)
    (root / "forbidden").symlink_to(outside, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    (root / "dangling").symlink_to(root / "missing")
    with client_for(monkeypatch) as client:
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "links"})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["file_count"] == 1
        response = client.get("/api/file/count", params={"path": str(outside), "request_id": "outside"})
        assert response.status_code == 403


def test_empty_directory(world, monkeypatch):
    (world / ".claude_code/workspace").mkdir(parents=True)
    with client_for(monkeypatch) as client:
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "empty"})
    assert response.status_code == 200
    assert response.json()["data"]["file_count"] == 0


@pytest.mark.asyncio
async def test_legacy_protocol_fails_explicitly():
    class LegacyFileService(FileService):
        pass
    with pytest.raises(NotImplementedError):
        await LegacyFileService().count_files("workspace")


@pytest.mark.parametrize("name", ["claude-code", "ClaudeCode", "claude_code"])
def test_alias_uses_canonical_count_layout(world, monkeypatch, name):
    (world / ".claude_code/workspace").mkdir(parents=True)
    with client_for(monkeypatch) as client:
        EngineManager.get_instance()._engine = name
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "alias"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["file_count"] == 0


def test_count_tracks_runtime_selection_and_ignores_unrelated_claude_config(world, monkeypatch):
    claw = world / "claw/workspace"
    claw.mkdir(parents=True)
    (claw / "one").write_text("one")
    (world / ".claude_code/workspace").mkdir(parents=True)
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(claw))
    with client_for(monkeypatch) as client:
        manager = EngineManager.get_instance()
        for name, count in [("claude_code", 0), ("openclaw", 1), ("claude-code", 0)]:
            manager._engine = name
            response = client.get("/api/file/count", params={"path": "workspace", "request_id": "switch"})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["file_count"] == count
        manager._engine = "openclaw"
        monkeypatch.setenv("CLAUDE_CODE_DEFAULT_CWD", "relative/invalid")
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "openclaw"})
        assert response.status_code == 200, response.text
        manager._engine = "unknown-runtime"
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "unknown"})
        assert response.status_code == 501
        assert response.json()["detail"] == "unsupported"


def test_malformed_counter_response_is_a_logged_failure(world, monkeypatch, caplog):
    class InvalidCounter:
        async def count_files(self, path):
            return None
    monkeypatch.setattr(EngineManager, "file_counter", property(lambda self: InvalidCounter()))
    with client_for(monkeypatch) as client, caplog.at_level("INFO"):
        response = client.get("/api/file/count", params={"path": "workspace", "request_id": "invalid"})
    assert response.status_code == 500
    assert response.json()["detail"] == "scan_failed"
    failure = next(record for record in caplog.records if record.msg == "engine.file_count.failure")
    assert failure.fields["request_id"] == "invalid"
