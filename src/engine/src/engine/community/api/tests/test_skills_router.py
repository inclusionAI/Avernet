"""Phase 3 — HTTP contract tests for `engine/api/skills/router.py`.

Existing test_skills_clean.py / test_skills_bindpath.py cover the FS-level
behavior of the OpenClawSkillsService via the router. This file covers the
dispatch contract: calls land on `manager.skills.*`, the capability guard
501s when the engine doesn't declare the skill bulk capabilities.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.skills.router import router as skills_router
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.skills.models import (
    CleanSymlinksResult,
    SyncSymlinksResult,
)
from engine.community.core.skills.layout_probe import (
    RuntimeLayoutInspection,
    RuntimeLayoutInspectionStatus,
)
from engine.community.manager import EngineManager


class _EngineWithSkills(BaseEngine):
    name = "rich"
    version = "1.0.0"
    _CAPABILITIES = EngineCapabilities(
        supported={
            Capability.SKILLS_SYNC_SYMLINKS,
            Capability.SKILLS_SYNC_BINDPATHS,
            Capability.SKILLS_CLEAN_SYMLINKS,
            Capability.SKILLS_CENTER_ENSURE,
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutSkills(BaseEngine):
    name = "lean"
    version = "0.1.0"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


def _install(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install(_EngineWithSkills)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install(_EngineWithoutSkills)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(skills_router)
    return TestClient(app)


class TestSyncSymlinks:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.sync_symlinks = AsyncMock(
            return_value=SyncSymlinksResult(
                total=1, created=["a"], base_dir="/tmp/x",
            )
        )
        rich_manager._active_engine._skills = plugin

        resp = client.post(
            "/api/skills/symlink",
            json={"symlinks": [{"source": "src", "target": "tgt"}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["created"] == ["a"]
        assert body["data"]["base_dir"] == "/tmp/x"
        plugin.sync_symlinks.assert_awaited_once()

    def test_501(self, lean_manager, client):
        resp = client.post("/api/skills/symlink", json={"symlinks": []})
        assert resp.status_code == 501


class TestSyncBindpaths:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.sync_bindpaths = AsyncMock(
            return_value=SyncSymlinksResult(total=0)
        )
        rich_manager._active_engine._skills = plugin

        resp = client.post(
            "/api/skills/symlink/bindpath",
            json={"symlinks": [], "clean_target_dir": False},
        )
        assert resp.status_code == 200
        # Verify clean_target_dir threaded through
        passed = plugin.sync_bindpaths.await_args.args[0]
        assert passed.clean_target_dir is False

    def test_501(self, lean_manager, client):
        resp = client.post(
            "/api/skills/symlink/bindpath",
            json={"symlinks": []},
        )
        assert resp.status_code == 501


class TestCleanSymlinksDispatch:
    def test_dispatches(self, rich_manager, client):
        plugin = MagicMock()
        plugin.clean_symlinks = AsyncMock(
            return_value=CleanSymlinksResult(
                directories_scanned=2, removed=["/a/b"],
            )
        )
        rich_manager._active_engine._skills = plugin

        resp = client.post(
            "/api/skills/symlink/clean",
            json={"directories": ["/abs/path"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["directories_scanned"] == 2
        assert body["data"]["removed"] == ["/a/b"]

    def test_501(self, lean_manager, client):
        resp = client.post(
            "/api/skills/symlink/clean", json={"directories": ["/x"]},
        )
        assert resp.status_code == 501

    def test_400_when_empty(self, rich_manager, client):
        plugin = MagicMock()
        plugin.clean_symlinks = AsyncMock(side_effect=ValueError("empty"))
        rich_manager._active_engine._skills = plugin

        resp = client.post("/api/skills/symlink/clean", json={"directories": []})
        assert resp.status_code == 400


def test_ensure_center_skills_route_success(rich_manager, client):
    """POST /api/skills/center/ensure dispatches to plugin.ensure_center_skills."""
    from engine.community.core.skills.models import (
        CenterEnsureFailure,
        CenterEnsureItem,
        CenterEnsureResult,
    )

    captured = {}

    class _FakePlugin:
        async def ensure_center_skills(self, req, auth=None):
            captured["items"] = list(req.items)
            return CenterEnsureResult(
                ok=[CenterEnsureItem(skill_uuid="u1", version="1.0.0")],
                failed=[CenterEnsureFailure(skill_uuid="u2", version="2.0.0", reason="missing")],
            )

    rich_manager._active_engine._skills = _FakePlugin()

    resp = client.post(
        "/api/skills/center/ensure",
        json={"items": [
            {"skill_uuid": "u1", "version": "1.0.0"},
            {"skill_uuid": "u2", "version": "2.0.0"},
        ]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["ok"] == [{"skill_uuid": "u1", "version": "1.0.0"}]
    assert body["data"]["failed"] == [
        {"skill_uuid": "u2", "version": "2.0.0", "reason": "missing"}
    ]
    assert len(captured["items"]) == 2


def test_runtime_layout_probe_has_no_engine_capability_dependency(client, monkeypatch):
    import importlib

    router_module = importlib.import_module("engine.community.api.skills.router")
    monkeypatch.setattr(
        router_module,
        "inspect_runtime_layout",
        lambda **_kwargs: RuntimeLayoutInspection(
            status=RuntimeLayoutInspectionStatus.READY,
            engine="openclaw",
            layout_contract_version="skills-pool-p3-v1",
            preparation_id="prep-1",
            evidence={"checks": {"pool_repo_mounted": True}},
        ),
    )

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "openclaw",
            "layout_contract_version": "skills-pool-p3-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "READY"
