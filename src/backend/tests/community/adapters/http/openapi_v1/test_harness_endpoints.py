"""Smoke tests for the public ``/openapi/v1/harness`` router.

A minimal FastAPI app hosts the harness router with the caller principal
overridden and the harness services bound to mocks via the injector — the same
harness the bots endpoint tests use. The real authenticator stays a stub;
``require_principal`` is overridden per test to supply (or withhold) a caller.

Why rollback (and the error branches) live here and not in the endpoint
framework: the parametrized runner drops any case whose id contains
``rollback``, so ``tests/community/endpoints/test_openapi_harness.py`` can only
*register* those operations — these tests are what actually execute them. The
branch tests below exist for the same reason: each one drives a guard or
exception path the framework's happy/error pairs do not reach.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
import agentclaw.community.adapters.http.openapi_v1.harness.router as harness_router_module
from agentclaw.community.adapters.http.openapi_v1.harness import build_harness_router
from agentclaw.community.adapters.http.openapi_v1.harness.router import router
from agentclaw.community.api.content_scanner_service import ContentScannerProtocol
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from agentclaw.community.api.patch_library_service import PatchLibraryProtocol
from agentclaw.community.api.patch_planner_service import PatchPlannerProtocol
from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
from agentclaw.community.core.harness.models import (
    Layer,
    PatchDefinition,
    PatchRecord,
    PatchStatus,
    PatchTarget,
)
from agentclaw.community.core.harness.services.patch_engine import PatchEngineError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.harness import (
    HarnessPatchRecordRepository,
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)

BOT = {"id": 77, "bot_id": "b1", "owner_id": "u1"}

_ONE_OP_CONTENT = json.dumps(
    [{"op": "update_md", "target": "BOT.md", "detail": {"dst_md_content": "x"}}]
)


@pytest.fixture
def bot_repo():
    m = MagicMock()
    m.get_by_id.return_value = BOT
    return m


@pytest.fixture
def collaborator():
    m = MagicMock()
    m.check_collaborator_permission.return_value = {"has_permission": False}
    return m


@pytest.fixture
def scan_repo():
    m = MagicMock()
    m.get_latest_dim_records.return_value = []
    m.list_dim_history.return_value = ([], 0)
    return m


@pytest.fixture
def patch_repo():
    return MagicMock()


@pytest.fixture
def record_repo():
    return MagicMock()


@pytest.fixture
def engine():
    return MagicMock()


@pytest.fixture
def lib():
    m = MagicMock()
    m.get_template_by_id.return_value = None
    return m


@pytest.fixture
def scanner():
    m = MagicMock()
    m._diagnostics = []
    return m


@pytest.fixture
def planner():
    return MagicMock()


@pytest.fixture
def client(bot_repo, collaborator, scan_repo, patch_repo, record_repo, engine, lib, scanner, planner):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotRepository, to=bot_repo)
            binder.bind(CollaboratorServiceProtocol, to=collaborator)
            binder.bind(HarnessScanRecordRepository, to=scan_repo)
            binder.bind(HarnessPatchRepository, to=patch_repo)
            binder.bind(HarnessPatchRecordRepository, to=record_repo)
            binder.bind(ContentScannerProtocol, to=scanner)
            binder.bind(PatchPlannerProtocol, to=planner)
            binder.bind(PatchEngineProtocol, to=engine)
            binder.bind(PatchLibraryProtocol, to=lib)

    app = FastAPI()
    app.include_router(router)
    mount_public_error_handlers(app)
    attach_injector(app, Injector([_M()]))
    app.dependency_overrides[require_principal] = lambda: {"user_id": "u1"}
    return user_scoped_client(app, "u1")


def test_missing_principal_is_401(client):
    client.app.dependency_overrides[require_principal] = lambda: None
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401000


def test_dim_report_happy_path_for_owner(client, scan_repo):
    scan_repo.get_latest_dim_records.return_value = [
        {"scan_dim": "skill", "health_score": 90, "grade": "A", "status": "completed"}
    ]
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200000
    assert body["data"]["bot_id"] == "b1"
    assert body["data"]["items"][0]["scan_dim"] == "skill"


def test_dim_history_happy_path_for_owner(client, scan_repo):
    resp = client.get("/openapi/v1/harness/bots/b1/dim-history?entity_id=u1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200000
    assert body["data"]["total"] == 0


def test_non_owner_without_grant_is_404(client, collaborator):
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u2", params={"user_id": "u2"})
    assert resp.status_code == 404


def test_unknown_bot_is_404(client, bot_repo):
    bot_repo.get_by_id.return_value = None
    resp = client.get("/openapi/v1/harness/bots/nope/dim-report?entity_id=u1")
    assert resp.status_code == 404


def test_collaborator_with_grant_passes(client, collaborator):
    collaborator.check_collaborator_permission.return_value = {"has_permission": True}
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u2", params={"user_id": "u2"})
    assert resp.status_code == 200


def test_build_harness_router_returns_the_module_router():
    assert build_harness_router() is router


# ── access-guard branches ────────────────────────────────────────────────────


def test_default_bot_short_circuits_the_lookup(client, bot_repo):
    resp = client.get("/openapi/v1/harness/bots/default/dim-report?entity_id=u1")
    assert resp.status_code == 200
    bot_repo.get_by_id.assert_not_called()


def test_bot_without_owner_is_404(client, bot_repo):
    bot_repo.get_by_id.return_value = {"id": 78, "bot_id": "b1"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 404


def test_collaborator_check_error_is_404(client, collaborator):
    collaborator.check_collaborator_permission.side_effect = RuntimeError("service down")
    client.app.dependency_overrides[require_principal] = lambda: {"user_id": "u2"}
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u2", params={"user_id": "u2"})
    assert resp.status_code == 404


# ── diagnose ──────────────────────────────────────────────────────────────────


async def _noop_scan(**_kwargs):
    return None


def test_diagnose_falls_back_to_progress_store_id(client, scan_repo, monkeypatch):
    """A falsy repository id is replaced by the next in-memory progress id."""
    scan_repo.create.return_value = None
    monkeypatch.setattr(harness_router_module, "_run_scan", _noop_scan)
    resp = client.post(
        "/openapi/v1/harness/bots/b1/diagnose",
        json={"entity_type": "staff", "entity_id": "u1"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "scanning"
    assert isinstance(data["scan_id"], int) and data["scan_id"] >= 1


# ── preview branch coverage ──────────────────────────────────────────────────


def test_preview_requires_patch_ids(client):
    resp = client.post(
        "/openapi/v1/harness/bots/b1/preview",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": []},
    )
    assert resp.status_code == 400


def test_preview_unknown_scan_is_404(client):
    resp = client.post(
        "/openapi/v1/harness/bots/b1/preview",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": [1], "scan_id": 424242},
    )
    assert resp.status_code == 404


def test_preview_unparseable_patch_content_is_400(client, patch_repo):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content="not-json"
    )
    resp = client.post(
        "/openapi/v1/harness/bots/b1/preview",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": [1]},
    )
    assert resp.status_code == 400


def test_preview_engine_failure_is_400(client, patch_repo, engine):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content=_ONE_OP_CONTENT
    )
    engine.preview = AsyncMock(side_effect=PatchEngineError("preview", "boom"))
    resp = client.post(
        "/openapi/v1/harness/bots/b1/preview",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": [1]},
    )
    assert resp.status_code == 400


# ── apply: record-id path and patch-path failure branches ────────────────────


def _planned_record() -> PatchRecord:
    return PatchRecord(
        bot_id="b1",
        entity_id="u1",
        patch_id=1,
        layer=Layer.L1,
        target=PatchTarget(files=["BOT.md"]),
        status=PatchStatus.PLANNED,
    )


def test_apply_by_record_id(client, record_repo, patch_repo, engine):
    record = _planned_record()
    record.id = 9
    record_repo.get_by_id.return_value = record
    engine.apply = AsyncMock(return_value=record)
    resp = client.post(
        "/openapi/v1/harness/bots/b1/apply",
        json={"entity_type": "staff", "entity_id": "u1", "record_id": 9},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 200000
    assert resp.json()["data"]["success"] is True
    engine.apply.assert_awaited_once()
    patch_repo.update_is_applied.assert_called_once_with(1, True)


def test_apply_missing_record_is_404(client, record_repo):
    record_repo.get_by_id.return_value = None
    resp = client.post(
        "/openapi/v1/harness/bots/b1/apply",
        json={"entity_type": "staff", "entity_id": "u1", "record_id": 99},
    )
    assert resp.status_code == 404


def test_apply_record_not_appliable_is_400(client, record_repo):
    record = _planned_record()
    record.status = PatchStatus.APPLIED
    record_repo.get_by_id.return_value = record
    resp = client.post(
        "/openapi/v1/harness/bots/b1/apply",
        json={"entity_type": "staff", "entity_id": "u1", "record_id": 9},
    )
    assert resp.status_code == 400


def test_apply_unknown_patch_is_404(client, patch_repo):
    patch_repo.get_by_id.return_value = None
    resp = client.post(
        "/openapi/v1/harness/bots/b1/apply",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": [999]},
    )
    assert resp.status_code == 404


def test_apply_tolerates_unparseable_content_and_record_repo_errors(client, patch_repo, record_repo, engine):
    """Bad patch content and a failing record repo still reach the engine."""
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content="not-json"
    )
    record_repo.get_by_patch_id.side_effect = RuntimeError("query down")
    record_repo.create.side_effect = RuntimeError("insert down")
    engine.apply = AsyncMock(side_effect=lambda **kwargs: kwargs["record"])
    resp = client.post(
        "/openapi/v1/harness/bots/b1/apply",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id_list": [1]},
    )
    assert resp.status_code == 200
    patch_repo.update_is_applied.assert_called_once_with(1, True)


# ── rollback: not executed by the endpoint-framework runner, covered here ────


def test_rollback_success(client, patch_repo, engine):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content=_ONE_OP_CONTENT
    )
    engine.rollback_by_patch = AsyncMock(return_value=(True, "rolled back"))
    resp = client.post(
        "/openapi/v1/harness/bots/b1/rollback",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == 200000
    assert resp.json()["data"]["success"] is True
    engine.rollback_by_patch.assert_awaited_once()
    patch_repo.update_is_applied.assert_called_once_with(1, False)


def test_rollback_unknown_patch_is_404(client, patch_repo):
    patch_repo.get_by_id.return_value = None
    resp = client.post(
        "/openapi/v1/harness/bots/b1/rollback",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id": 999},
    )
    assert resp.status_code == 404


def test_rollback_unparseable_content_is_400(client, patch_repo):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content="not-json"
    )
    resp = client.post(
        "/openapi/v1/harness/bots/b1/rollback",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id": 1},
    )
    assert resp.status_code == 400


def test_rollback_without_operations_is_400(client, patch_repo):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content="[]"
    )
    resp = client.post(
        "/openapi/v1/harness/bots/b1/rollback",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id": 1},
    )
    assert resp.status_code == 400


def test_rollback_engine_failure_is_400(client, patch_repo, engine):
    patch_repo.get_by_id.return_value = PatchDefinition(
        template_id=0, name="p", layer=Layer.L1, id=1, content=_ONE_OP_CONTENT
    )
    engine.rollback_by_patch = AsyncMock(return_value=(False, "engine said no"))
    resp = client.post(
        "/openapi/v1/harness/bots/b1/rollback",
        json={"entity_type": "staff", "entity_id": "u1", "patch_id": 1},
    )
    assert resp.status_code == 400


# ── dim-report / dim-history rows carrying patch references ──────────────────


def _patch(patch_id: int, content: str | None, name: str = "p", advise: str | None = None) -> PatchDefinition:
    return PatchDefinition(
        template_id=0, name=name, layer=Layer.L1, id=patch_id, content=content, advise=advise
    )


def test_dim_report_rows_with_patches(client, scan_repo, patch_repo):
    scan_repo.get_latest_dim_records.return_value = [
        {"scan_dim": "skill", "scan_type": "full", "status": "completed", "patch_ids": "[1, 2, 3]"},
        {"scan_dim": "mcp", "scan_type": "full", "status": "completed", "patch_ids": "not-json"},
    ]
    patch_repo.list_by_ids.return_value = [
        _patch(1, _ONE_OP_CONTENT, advise=json.dumps({"advise_content": "review manually"})),
        _patch(2, "not-json"),
    ]
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    patches = items[0]["patches"]
    assert [p["patch_id"] for p in patches] == [1, 2]  # id 3 is unknown and skipped
    assert patches[0]["operations"][0]["op"] == "update_md"
    assert patches[0]["is_advise"] is True
    assert patches[1]["operations"] == []


def test_dim_report_tolerates_patch_fetch_failure(client, scan_repo, patch_repo):
    scan_repo.get_latest_dim_records.return_value = [
        {"scan_dim": "skill", "scan_type": "full", "patch_ids": "[1]"},
    ]
    patch_repo.list_by_ids.side_effect = RuntimeError("db down")
    resp = client.get("/openapi/v1/harness/bots/b1/dim-report?entity_id=u1")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"][0]["patches"] == []


def test_dim_history_rows_with_patches(client, scan_repo, patch_repo):
    rows = [
        {"id": 7, "scan_dim": "skill", "scan_type": "full", "status": "completed", "patch_ids": "[5]"},
        # Same patch id again: the per-request op cache answers the second row.
        {"id": 8, "scan_dim": "skill", "scan_type": "full", "status": "completed", "patch_ids": "[5]"},
        # A patch with unparseable content, and an id the repository does not hold.
        {"id": 9, "scan_dim": "skill", "scan_type": "full", "status": "completed", "patch_ids": "[6, 99]"},
        {"id": 10, "scan_dim": "skill", "scan_type": "full", "status": "completed", "patch_ids": "not-json"},
    ]
    scan_repo.list_dim_history.return_value = (rows, 4)
    patch_repo.list_by_ids.return_value = [_patch(5, _ONE_OP_CONTENT), _patch(6, "not-json")]
    resp = client.get("/openapi/v1/harness/bots/b1/dim-history?entity_id=u1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    assert len(data["items"]) == 4
    assert data["items"][0]["patches"][0]["patch_id"] == 5
    assert data["items"][0]["patches"][0]["operations"][0]["op"] == "update_md"
    assert data["items"][1]["patches"][0]["patch_id"] == 5
    assert [p["patch_id"] for p in data["items"][2]["patches"]] == [6]


def test_dim_history_tolerates_patch_fetch_failure(client, scan_repo, patch_repo):
    scan_repo.list_dim_history.return_value = (
        [{"id": 7, "scan_dim": "skill", "patch_ids": "[5]"}],
        1,
    )
    patch_repo.list_by_ids.side_effect = RuntimeError("db down")
    resp = client.get("/openapi/v1/harness/bots/b1/dim-history?entity_id=u1")
    assert resp.status_code == 200
    assert resp.json()["data"]["items"][0]["patches"] == []
