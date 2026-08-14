"""Phase 3 — HTTP contract tests for `engine/api/skills/router.py`.

Existing test_skills_clean.py / test_skills_bindpath.py cover the FS-level
behavior of the OpenClawSkillsService via the router. This file covers the
dispatch contract: calls land on `manager.skills.*`, the capability guard
501s when the engine doesn't declare the skill bulk capabilities.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from engine.community.api.skills.router import router as skills_router
from engine.community.core.adapters.openclaw.skills import (
    OpenClawSkillsAdapter,
)
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.exceptions import (
    CapabilityNotSupportedError,
)
from engine.community.core.engine.registry import EngineRegistry
from engine.community.core.skills.exceptions import (
    InvalidPoolMappingRequestError,
)
from engine.community.core.skills.models import (
    CleanSymlinksResult,
    PoolLayoutActivationResult,
    PoolLayoutActivationStatus,
    PoolLayoutProbeResult,
    PoolLayoutProbeStatus,
    PoolMappingPublishResult,
    PoolMappingSourceLayout,
    PoolMappingVerificationResult,
    PoolQuarantineCleanupResult,
    PoolSkillMappingIntent,
    SymlinkItem,
    SyncSymlinksResult,
)
from engine.community.manager import EngineManager
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
                total=1,
                created=["a"],
                base_dir="/tmp/x",
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
        plugin.sync_bindpaths = AsyncMock(return_value=SyncSymlinksResult(total=0))
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
                directories_scanned=2,
                removed=["/a/b"],
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
            "/api/skills/symlink/clean",
            json={"directories": ["/x"]},
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
                failed=[
                    CenterEnsureFailure(
                        skill_uuid="u2", version="2.0.0", reason="missing"
                    )
                ],
            )

    rich_manager._active_engine._skills = _FakePlugin()

    resp = client.post(
        "/api/skills/center/ensure",
        json={
            "items": [
                {"skill_uuid": "u1", "version": "1.0.0"},
                {"skill_uuid": "u2", "version": "2.0.0"},
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["ok"] == [{"skill_uuid": "u1", "version": "1.0.0"}]
    assert body["data"]["failed"] == [
        {"skill_uuid": "u2", "version": "2.0.0", "reason": "missing"}
    ]
    assert len(captured["items"]) == 2


def test_runtime_layout_probe_has_no_engine_capability_dependency(client, rich_manager):
    plugin = MagicMock()
    plugin.probe_pool_layout = AsyncMock(
        return_value=PoolLayoutProbeResult(
            status=PoolLayoutProbeStatus.READY,
            engine="openclaw",
            layout_contract_version="skills-pool-p3-v1",
            preparation_id="prep-1",
            evidence={"checks": {"pool_repo_mounted": True}},
        )
    )
    rich_manager._active_engine._skills = plugin

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "openclaw",
            "layout_contract_version": "skills-pool-p3-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "READY"
    plugin.probe_pool_layout.assert_awaited_once()


def test_runtime_layout_probe_rejects_unknown_engine_before_dispatch(
    client, rich_manager
):
    plugin = MagicMock()
    plugin.probe_pool_layout = AsyncMock()
    rich_manager._active_engine._skills = plugin

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "unknown-engine",
            "layout_contract_version": "skills-pool-p3-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "INVALID",
        "engine": "unknown-engine",
        "layout_contract_version": "skills-pool-p3-v1",
        "preparation_id": None,
        "evidence": {
            "reason": "layout_identity_invalid",
            "error_type": "SkillLayoutResolutionError",
        },
    }
    plugin.probe_pool_layout.assert_not_awaited()


def test_runtime_layout_probe_rejects_unknown_contract_before_dispatch(
    client, rich_manager
):
    plugin = MagicMock()
    plugin.probe_pool_layout = AsyncMock()
    rich_manager._active_engine._skills = plugin

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "hermes",
            "layout_contract_version": "skills-pool-p3-v999",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "INVALID",
        "engine": "hermes",
        "layout_contract_version": "skills-pool-p3-v999",
        "preparation_id": None,
        "evidence": {
            "reason": "layout_identity_invalid",
            "error_type": "UnsupportedLayoutContractError",
        },
    }
    plugin.probe_pool_layout.assert_not_awaited()


def test_runtime_layout_probe_rejects_plugin_engine_mismatch(client, rich_manager):
    plugin = MagicMock()
    plugin.probe_pool_layout = AsyncMock(
        return_value=PoolLayoutProbeResult(
            status=PoolLayoutProbeStatus.READY,
            engine="hermes",
            layout_contract_version="skills-pool-p3-v1",
            preparation_id="prep-1",
            evidence={"checks": {"marker_valid": True}},
        )
    )
    rich_manager._active_engine._skills = plugin

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "openclaw",
            "layout_contract_version": "skills-pool-p3-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "INVALID",
        "engine": "openclaw",
        "layout_contract_version": "skills-pool-p3-v1",
        "preparation_id": None,
        "evidence": {
            "reason": "runtime_engine_mismatch",
            "actual_engine": "hermes",
        },
    }
    plugin.probe_pool_layout.assert_awaited_once()


def test_runtime_layout_probe_rejects_real_openclaw_plugin_engine_mismatch(
    client,
    rich_manager,
) -> None:
    rich_manager._active_engine._skills = OpenClawSkillsAdapter(OpenClawPluginImpl())

    response = client.post(
        "/api/skills/layout/probe",
        json={
            "engine": "hermes",
            "layout_contract_version": "skills-pool-p3-v1",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "INVALID",
        "engine": "hermes",
        "layout_contract_version": "skills-pool-p3-v1",
        "preparation_id": None,
        "evidence": {
            "reason": "runtime_engine_mismatch",
            "actual_engine": "openclaw",
        },
    }


def test_pool_activation_and_mapping_routes_are_capability_independent(
    client, rich_manager
):
    plugin = MagicMock()
    plugin.activate_pool_layout = AsyncMock(
        return_value=PoolLayoutActivationResult(
            committed=True,
            status=PoolLayoutActivationStatus.COMMITTED,
            evidence={"bridge": "valid"},
        ),
    )
    plugin.rollback_pool_layout = AsyncMock(
        return_value=PoolLayoutActivationResult(
            committed=True,
            status=PoolLayoutActivationStatus.COMMITTED,
            evidence={"source": "current_pool"},
        ),
    )
    plugin.cleanup_pool_quarantine = AsyncMock(
        return_value=PoolQuarantineCleanupResult(
            status="CLEANED",
            evidence={"path_absent": True},
        ),
    )
    plugin.publish_pool_mappings = AsyncMock(
        return_value=PoolMappingPublishResult(
            published=True,
            evidence={"total": 1},
        ),
    )
    plugin.verify_pool_mappings = AsyncMock(
        return_value=PoolMappingVerificationResult(
            valid=True,
            evidence={"checked": 1},
        ),
    )
    rich_manager._active_engine._skills = plugin
    mappings = [{"source": "/pool/a", "target": "/skills/a"}]

    activation = client.post(
        "/api/skills/layout/activate",
        json={
            "migration_generation": "generation-1",
            "preparation_id": "preparation-1",
            "registered_local_names": ["a"],
            "mappings": mappings,
        },
    )
    rollback = client.post(
        "/api/skills/layout/rollback",
        json={
            "rollback_generation": "rollback-1",
            "registered_local_names": ["a"],
        },
    )
    cleanup = client.post(
        "/api/skills/layout/quarantine/cleanup",
        json={"migration_generation": "generation-1"},
    )
    published = client.post(
        "/api/skills/layout/mappings/publish",
        json={"mappings": mappings, "source_layout": "legacy"},
    )
    verified = client.post(
        "/api/skills/layout/mappings/verify",
        json={"mappings": mappings, "source_layout": "legacy"},
    )

    assert activation.json()["data"]["committed"] is True
    assert rollback.json()["data"]["committed"] is True
    assert cleanup.json()["data"]["status"] == "CLEANED"
    assert published.json()["data"]["published"] is True
    assert verified.json()["data"]["valid"] is True
    plugin.activate_pool_layout.assert_awaited_once()
    plugin.rollback_pool_layout.assert_awaited_once()
    plugin.cleanup_pool_quarantine.assert_awaited_once()
    plugin.publish_pool_mappings.assert_awaited_once_with(
        [
            SymlinkItem(source="/pool/a", target="/skills/a"),
        ],
        source_layout=PoolMappingSourceLayout.LEGACY,
    )
    plugin.verify_pool_mappings.assert_awaited_once_with(
        [
            SymlinkItem(source="/pool/a", target="/skills/a"),
        ],
        source_layout=PoolMappingSourceLayout.LEGACY,
    )


def test_pool_mapping_routes_propagate_logical_v2_contract(client, rich_manager):
    plugin = MagicMock()
    plugin.activate_pool_layout = AsyncMock(
        return_value=PoolLayoutActivationResult(
            committed=True,
            status=PoolLayoutActivationStatus.COMMITTED,
            evidence={},
        ),
    )
    plugin.publish_pool_mappings = AsyncMock(
        return_value=PoolMappingPublishResult(published=True, evidence={}),
    )
    plugin.verify_pool_mappings = AsyncMock(
        return_value=PoolMappingVerificationResult(valid=True, evidence={}),
    )
    rich_manager._active_engine._skills = plugin
    mapping = {
        "corpus": "repo",
        "relative_path": "business/reviewer",
        "link_name": "reviewer",
    }
    retired_mapping = {
        "corpus": "repo",
        "relative_path": "legacy/writer",
        "link_name": "writer",
    }
    version = "skills-pool-mapping-v2"

    activation = client.post(
        "/api/skills/layout/activate",
        json={
            "migration_generation": "generation-1",
            "preparation_id": "preparation-1",
            "registered_local_names": [],
            "mapping_contract_version": version,
            "mappings": [mapping],
        },
    )
    published = client.post(
        "/api/skills/layout/mappings/publish",
        json={
            "mapping_contract_version": version,
            "mappings": [mapping],
            "retired_mappings": [retired_mapping],
        },
    )
    verified = client.post(
        "/api/skills/layout/mappings/verify",
        json={
            "mapping_contract_version": version,
            "mappings": [mapping],
            "retired_mappings": [retired_mapping],
        },
    )

    assert (
        activation.status_code == published.status_code == verified.status_code == 200
    )
    intent = PoolSkillMappingIntent(
        corpus="repo",
        relative_path="business/reviewer",
        link_name="reviewer",
    )
    retired_intent = PoolSkillMappingIntent(
        corpus="repo",
        relative_path="legacy/writer",
        link_name="writer",
    )
    request = plugin.activate_pool_layout.await_args.args[0]
    assert request.mapping_contract_version == version
    assert request.mappings == [intent]
    plugin.publish_pool_mappings.assert_awaited_once_with(
        [intent],
        mapping_contract_version=version,
        retired_mappings=[retired_intent],
    )
    plugin.verify_pool_mappings.assert_awaited_once_with(
        [intent],
        mapping_contract_version=version,
        retired_mappings=[retired_intent],
    )


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "activate_pool_layout",
            "/api/skills/layout/activate",
            {
                "migration_generation": "generation-1",
                "preparation_id": "preparation-1",
                "registered_local_names": [],
                "mappings": [],
            },
        ),
        (
            "publish_pool_mappings",
            "/api/skills/layout/mappings/publish",
            {"mappings": []},
        ),
        (
            "verify_pool_mappings",
            "/api/skills/layout/mappings/verify",
            {"mappings": []},
        ),
    ],
)
def test_pool_mapping_routes_map_invalid_request_errors_to_400(
    client,
    rich_manager,
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    plugin = MagicMock()
    setattr(
        plugin,
        method,
        AsyncMock(
            side_effect=InvalidPoolMappingRequestError(
                "mapping_contract_version is unsupported"
            )
        ),
    )
    rich_manager._active_engine._skills = plugin

    response = client.post(path, json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == ("mapping_contract_version is unsupported")


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/skills/layout/activate",
            {
                "migration_generation": "generation-1",
                "preparation_id": "preparation-1",
                "registered_local_names": [],
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [{"source": "/pool/a", "target": "/active/a"}],
            },
        ),
        (
            "/api/skills/layout/mappings/publish",
            {
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [{"source": "/pool/a", "target": "/active/a"}],
            },
        ),
        (
            "/api/skills/layout/mappings/verify",
            {
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [{"source": "/pool/a", "target": "/active/a"}],
            },
        ),
    ],
)
def test_pool_mapping_routes_reject_v2_physical_shape_via_real_adapter(
    client,
    rich_manager,
    path: str,
    payload: dict[str, object],
) -> None:
    rich_manager._active_engine._skills = OpenClawSkillsAdapter(OpenClawPluginImpl())

    response = client.post(path, json=payload)

    assert response.status_code == 400
    assert "logical mapping" in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "activate_pool_layout",
            "/api/skills/layout/activate",
            {
                "migration_generation": "generation-1",
                "preparation_id": "preparation-1",
                "registered_local_names": [],
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [
                    {
                        "corpus": "unknown",
                        "relative_path": "writer",
                        "link_name": "writer",
                    }
                ],
            },
        ),
        (
            "publish_pool_mappings",
            "/api/skills/layout/mappings/publish",
            {
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [
                    {
                        "corpus": "unknown",
                        "relative_path": "writer",
                        "link_name": "writer",
                    }
                ],
            },
        ),
        (
            "verify_pool_mappings",
            "/api/skills/layout/mappings/verify",
            {
                "mapping_contract_version": "skills-pool-mapping-v2",
                "mappings": [
                    {
                        "corpus": "unknown",
                        "relative_path": "writer",
                        "link_name": "writer",
                    }
                ],
            },
        ),
    ],
)
def test_pool_mapping_routes_reject_unknown_corpus_at_schema_boundary(
    client,
    rich_manager,
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    plugin = MagicMock()
    setattr(plugin, method, AsyncMock())
    rich_manager._active_engine._skills = plugin

    response = client.post(path, json=payload)

    assert response.status_code == 422
    getattr(plugin, method).assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "probe_pool_layout",
            "/api/skills/layout/probe",
            {
                "engine": "claude_code",
                "layout_contract_version": "skills-pool-p3-v1",
            },
        ),
        (
            "activate_pool_layout",
            "/api/skills/layout/activate",
            {
                "migration_generation": "generation-1",
                "preparation_id": "preparation-1",
                "registered_local_names": [],
                "mappings": [],
            },
        ),
        (
            "rollback_pool_layout",
            "/api/skills/layout/rollback",
            {
                "rollback_generation": "rollback-1",
                "registered_local_names": [],
            },
        ),
        (
            "cleanup_pool_quarantine",
            "/api/skills/layout/quarantine/cleanup",
            {"migration_generation": "generation-1"},
        ),
        (
            "publish_pool_mappings",
            "/api/skills/layout/mappings/publish",
            {"mappings": []},
        ),
        (
            "verify_pool_mappings",
            "/api/skills/layout/mappings/verify",
            {"mappings": []},
        ),
    ],
)
def test_pool_routes_map_unsupported_engine_to_501(
    client,
    rich_manager,
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    plugin = MagicMock()
    setattr(
        plugin,
        method,
        AsyncMock(
            side_effect=CapabilityNotSupportedError(
                "claude_code",
                Capability.SKILLS_SYNC_BINDPATHS,
            )
        ),
    )
    rich_manager._active_engine._skills = plugin

    response = client.post(path, json=payload)

    assert response.status_code == 501
