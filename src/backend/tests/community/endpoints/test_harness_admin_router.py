"""Tests for admin-only harness endpoints.

Tests the following endpoints:
- POST /api/harness/admin/diagnose  — admin-gated diagnose
- POST /api/harness/admin/apply     — admin-gated apply
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.core.harness.models import (
    Layer,
    PatchDefinition,
    PatchOperation,
    PatchRecord,
    PatchRecord as DomainPatchRecord,
    PatchStatus,
    PatchTarget,
    FindingsReport,
)
from agentclaw.community.core.harness.repository_protocol import (
    HarnessPatchRecordRepository,
    HarnessPatchRepository,
    HarnessScanRecordRepository,
)
from agentclaw.community.api.patch_engine_service import PatchEngineProtocol
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.fixtures import app_with_testing_modules


# ── Local client fixture with all DB tables ────────────────────────
# The root conftest ``client`` does NOT call ``Base.metadata.create_all``,
# so harness-specific tables (ac_harness_patch_record, etc.) are absent.
# We override ``client`` here to depend on ``app_with_testing_modules``
# which creates the full in-memory schema, including harness tables.


@pytest.fixture
def client(app_with_testing_modules):
    """TestClient backed by a per-test injector with all tables created."""
    return TestClient(app_with_testing_modules)


# ── Seed helpers ────────────────────────────────────────────────


def _seed_admin_user(world):
    """Seed the admin user (staffId=100000) and a bot they can diagnose."""
    make_staff_user(world, user_id="100000")
    make_bot(world, bot_id="bot_test", owner_id="100000")


def _seed_non_admin_user(world):
    """Seed a non-admin user and a bot."""
    make_staff_user(world, user_id="u_not_admin")
    make_bot(world, bot_id="bot_test", owner_id="u_not_admin")


# ── POST /api/harness/admin/diagnose ───────────────────────────


@endpoint_test(
    method="POST",
    path="/api/harness/admin/diagnose",
    scenario="admin_diagnose_ok",
    input=CaseInput(
        headers={"x-user-id": "100000"},
        json_body={
            "bot_id": "bot_test",
            "entity_id": "100000",
            "entity_type": "staff",
            "scan_type": "full",
            "layer": "L1",
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectSuccess(
        status=202,
        json_contains={"bot_id": "bot_test", "status": "scanning"},
    ),
)
def admin_diagnose_ok():
    """POST /admin/diagnose with admin user returns 202 and scan_id."""


@endpoint_test(
    method="POST",
    path="/api/harness/admin/diagnose",
    scenario="admin_diagnose_forbidden",
    input=CaseInput(
        headers={"x-user-id": "u_not_admin"},
        json_body={
            "bot_id": "bot_test",
            "entity_id": "u_not_admin",
            "entity_type": "staff",
        },
    ),
    seed=_seed_non_admin_user,
    expect=ExpectError(
        status=403,
    ),
)
def admin_diagnose_forbidden():
    """POST /admin/diagnose with non-admin user returns 403."""


@endpoint_test(
    method="POST",
    path="/api/harness/admin/diagnose",
    scenario="admin_diagnose_missing_fields",
    input=CaseInput(
        headers={"x-user-id": "100000"},
        json_body={
            "bot_id": "",
            "entity_id": "",
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectError(
        status=400,
    ),
)
def admin_diagnose_missing_fields():
    """POST /admin/diagnose with empty bot_id/entity_id returns 400."""


# ── POST /api/harness/admin/apply ───────────────────────────────


@endpoint_test(
    method="POST",
    path="/api/harness/admin/apply",
    scenario="admin_apply_forbidden",
    input=CaseInput(
        headers={"x-user-id": "u_not_admin"},
        json_body={
            "bot_id": "bot_test",
            "entity_id": "u_not_admin",
            "patch_id_list": [1],
        },
    ),
    seed=_seed_non_admin_user,
    expect=ExpectError(
        status=403,
    ),
)
def admin_apply_forbidden():
    """POST /admin/apply with non-admin user returns 403."""


@endpoint_test(
    method="POST",
    path="/api/harness/admin/apply",
    scenario="admin_apply_missing_params",
    input=CaseInput(
        headers={"x-user-id": "100000"},
        json_body={
            "bot_id": "bot_test",
            "entity_id": "100000",
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectError(
        status=400,
    ),
)
def admin_apply_missing_params():
    """POST /admin/apply without record_id or patch_id_list returns 400."""


@endpoint_test(
    method="POST",
    path="/api/harness/admin/apply",
    scenario="admin_apply_patch_not_found",
    input=CaseInput(
        headers={"x-user-id": "100000"},
        json_body={
            "bot_id": "bot_test",
            "entity_id": "100000",
            "patch_id_list": [999999],
        },
    ),
    seed=_seed_admin_user,
    expect=ExpectError(
        status=404,
    ),
)
def admin_apply_patch_not_found():
    """POST /admin/apply as admin with non-existent patch_id returns 404.

    This verifies admin auth passes (not 403) and the request reaches
    the business logic layer (which returns 404 for missing patch).
    """


# ── Direct API tests (non-declarative, for branches not reachable
#    via the endpoint_test framework) ────────────────────────────


class TestAdminDiagnoseConflict:
    """Test the 409 conflict branch when a scan is already active."""

    def test_diagnose_conflict(self, client):
        """When an active scan exists, second diagnose returns 409."""
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessScanRecordRepository,
        )
        from agentclaw.community.di import Injected

        scan_repo = client.app.state.injector.get(HarnessScanRecordRepository)
        # Make has_active_scan return True
        with patch.object(
            type(scan_repo), "has_active_scan", return_value=True
        ):
            resp = client.post(
                "/api/harness/admin/diagnose",
                json={
                    "bot_id": "bot_conflict",
                    "entity_id": "100000",
                    "entity_type": "staff",
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 409
        assert "诊断中" in resp.json().get("detail", "")


class TestAdminApplyByRecordId:
    """Test admin/apply with record_id mode."""

    def test_apply_record_not_found(self, client):
        """Applying with non-existent record_id returns 404."""
        resp = client.post(
            "/api/harness/admin/apply",
            json={
                "bot_id": "bot_test",
                "entity_id": "100000",
                "record_id": 999999,
            },
            headers={"x-user-id": "100000"},
        )
        assert resp.status_code == 404

    def test_apply_record_bad_status(self, client):
        """Applying a record already applied returns 400."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchStatus,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
        )

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        # Create a record with APPLIED status
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.APPLIED,
        )
        record_id = patch_record_repo.create(record)

        resp = client.post(
            "/api/harness/admin/apply",
            json={
                "bot_id": "bot_test",
                "entity_id": "100000",
                "record_id": record_id,
            },
            headers={"x-user-id": "100000"},
        )
        assert resp.status_code == 400


class TestAdminApplyPatchIdListSuccess:
    """Test admin/apply with patch_id_list mode — happy path via mock."""

    def test_apply_patch_id_list_success(self, client):
        """Applying patches by patch_id_list with mocked engine returns success."""
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRepository,
            HarnessPatchRecordRepository,
        )

        patch_repo = client.app.state.injector.get(HarnessPatchRepository)
        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )

        # Create a patch definition in the repo
        patch_def = PatchDefinition(
            template_id=0,
            name="AGENTS.md",
            layer=Layer.L1,
            content='[{"op": "update_md", "target": "AGENTS.md", "detail": {"dst_content": "# test"}}]',
        )
        patch_id = patch_repo.create(patch_def)

        # Mock the patch_record_repo.get_by_patch_id to return None (no existing record)
        with patch.object(
            type(patch_record_repo),
            "get_by_patch_id",
            return_value=None,
        ):
            # Mock patch_record_repo.create to return a valid record_id
            with patch.object(
                type(patch_record_repo),
                "create",
                return_value=1,
            ):
                # Mock engine.apply to return a successful record
                mock_engine = client.app.state.injector.get(PatchEngineProtocol)
                applied_record = DomainPatchRecord(
                    bot_id="bot_test",
                    entity_id="100000",
                    patch_id=patch_id,
                    layer=Layer.L1,
                    target=PatchTarget(files=["AGENTS.md"]),
                    status=PatchStatus.APPLIED,
                )
                applied_record.id = 1
                with patch.object(
                    type(mock_engine),
                    "apply",
                    new_callable=AsyncMock,
                    return_value=applied_record,
                ):
                    resp = client.post(
                        "/api/harness/admin/apply",
                        json={
                            "bot_id": "bot_test",
                            "entity_id": "100000",
                            "patch_id_list": [patch_id],
                        },
                        headers={"x-user-id": "100000"},
                    )
        assert resp.status_code == 200
        assert resp.json().get("success") is True


class TestAdminApplyRecordIdSuccess:
    """Test admin/apply with record_id mode — happy path via mock."""

    def test_apply_record_id_success(self, client):
        """Applying a PLANNED record by record_id with mocked engine returns success."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchStatus,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
            HarnessPatchRepository,
        )

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        patch_repo = client.app.state.injector.get(HarnessPatchRepository)

        # Create a PLANNED record
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.PLANNED,
            operations=[
                PatchOperation(
                    op="update_md",
                    target="AGENTS.md",
                    detail={"dst_content": "# updated"},
                )
            ],
        )
        record_id = patch_record_repo.create(record)

        # Mock engine.apply to return APPLIED record
        mock_engine = client.app.state.injector.get(PatchEngineProtocol)
        applied_record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.APPLIED,
        )
        applied_record.id = record_id
        with patch.object(
            type(mock_engine),
            "apply",
            new_callable=AsyncMock,
            return_value=applied_record,
        ):
            resp = client.post(
                "/api/harness/admin/apply",
                json={
                    "bot_id": "bot_test",
                    "entity_id": "100000",
                    "record_id": record_id,
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 200
        assert resp.json().get("success") is True


class TestAdminApplyPatchEngineError:
    """Test admin/apply when engine raises PatchEngineError."""

    def test_apply_engine_error(self, client):
        """PatchEngineError from engine.apply returns 400."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchStatus,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
        )
        from agentclaw.community.core.harness.services.patch_engine import PatchEngineError

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        # Create a PLANNED record
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.PLANNED,
        )
        record_id = patch_record_repo.create(record)

        # Mock engine.apply to raise PatchEngineError
        mock_engine = client.app.state.injector.get(PatchEngineProtocol)
        with patch.object(
            type(mock_engine),
            "apply",
            new_callable=AsyncMock,
            side_effect=PatchEngineError("apply", "something went wrong"),
        ):
            resp = client.post(
                "/api/harness/admin/apply",
                json={
                    "bot_id": "bot_test",
                    "entity_id": "100000",
                    "record_id": record_id,
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 400
        assert "something went wrong" in resp.json().get("detail", "")


class TestAdminApplyRecordPreviewed:
    """Test admin/apply with a record in PREVIEWED status (should succeed)."""

    def test_apply_previewed_record_success(self, client):
        """A PREVIEWED record should be accepted for apply, same as PLANNED."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchStatus,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
        )

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        # Create a PREVIEWED record
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.PREVIEWED,
            operations=[
                PatchOperation(
                    op="update_md",
                    target="AGENTS.md",
                    detail={"dst_content": "# previewed update"},
                )
            ],
        )
        record_id = patch_record_repo.create(record)

        # Mock engine.apply to return APPLIED record
        mock_engine = client.app.state.injector.get(PatchEngineProtocol)
        applied_record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.APPLIED,
        )
        applied_record.id = record_id
        with patch.object(
            type(mock_engine),
            "apply",
            new_callable=AsyncMock,
            return_value=applied_record,
        ):
            resp = client.post(
                "/api/harness/admin/apply",
                json={
                    "bot_id": "bot_test",
                    "entity_id": "100000",
                    "record_id": record_id,
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 200
        assert resp.json().get("success") is True


class TestAdminApplyServerError:
    """Test admin/apply when engine raises an unexpected Exception."""

    def test_apply_generic_error_returns_500(self, client):
        """A generic Exception from engine.apply returns 500."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchStatus,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
        )

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        # Create a PLANNED record
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=PatchStatus.PLANNED,
        )
        record_id = patch_record_repo.create(record)

        # Mock engine.apply to raise a generic Exception
        mock_engine = client.app.state.injector.get(PatchEngineProtocol)
        with patch.object(
            type(mock_engine),
            "apply",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected failure"),
        ):
            resp = client.post(
                "/api/harness/admin/apply",
                json={
                    "bot_id": "bot_test",
                    "entity_id": "100000",
                    "record_id": record_id,
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 500


class TestAdminApplyRecordOtherBadStatus:
    """Test admin/apply rejects records in non-applyable statuses beyond APPLIED."""

    @pytest.mark.parametrize("bad_status", [
        PatchStatus.ROLLED_BACK,
        PatchStatus.FAILED,
    ])
    def test_apply_bad_status_rejected(self, client, bad_status):
        """Records with ROLLED_BACK or FAILED status cannot be applied."""
        from agentclaw.community.core.harness.models import (
            PatchRecord as DomainPatchRecord,
            PatchTarget,
        )
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRecordRepository,
        )

        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )
        record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=0,
            layer=Layer.L1,
            target=PatchTarget(files=["AGENTS.md"]),
            status=bad_status,
        )
        record_id = patch_record_repo.create(record)

        resp = client.post(
            "/api/harness/admin/apply",
            json={
                "bot_id": "bot_test",
                "entity_id": "100000",
                "record_id": record_id,
            },
            headers={"x-user-id": "100000"},
        )
        assert resp.status_code == 400
        detail = resp.json().get("detail", "")
        assert "cannot apply" in detail.lower() or bad_status.value in detail


class TestAdminDiagnoseSuccess:
    """Test admin/diagnose success path with response structure verification."""

    def test_diagnose_returns_scan_id(self, client):
        """Successful diagnose returns 202 with scan_id, bot_id, and scanning status."""
        resp = client.post(
            "/api/harness/admin/diagnose",
            json={
                "bot_id": "bot_diag_ok",
                "entity_id": "100000",
                "entity_type": "staff",
                "scan_type": "full",
                "layer": "L1",
            },
            headers={"x-user-id": "100000"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body.get("bot_id") == "bot_diag_ok"
        assert body.get("status") == "scanning"
        assert "scan_id" in body
        assert isinstance(body["scan_id"], int)
        assert body.get("success") is True

    def test_diagnose_default_values(self, client):
        """Diagnose with minimal fields uses defaults (scan_type=full, layer=L1)."""
        resp = client.post(
            "/api/harness/admin/diagnose",
            json={
                "bot_id": "bot_diag_defaults",
                "entity_id": "100000",
                "entity_type": "staff",
            },
            headers={"x-user-id": "100000"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body.get("status") == "scanning"


class TestAdminApplyPatchIdListEmptyContent:
    """Test admin/apply with patch_id_list when patch has empty content."""

    def test_apply_patch_empty_content(self, client):
        """A patch with no content (empty operations list) is still submitted
        to engine.apply — the endpoint does not enforce non-empty ops."""
        from agentclaw.community.core.harness.repository_protocol import (
            HarnessPatchRepository,
            HarnessPatchRecordRepository,
        )

        patch_repo = client.app.state.injector.get(HarnessPatchRepository)
        patch_record_repo = client.app.state.injector.get(
            HarnessPatchRecordRepository
        )

        # Create a patch definition with empty content (no operations)
        patch_def = PatchDefinition(
            template_id=0,
            name="EmptyPatch",
            layer=Layer.L1,
            content=None,
        )
        patch_id = patch_repo.create(patch_def)

        # Mock get_by_patch_id to return None, create to return a record_id
        mock_engine = client.app.state.injector.get(PatchEngineProtocol)
        applied_record = DomainPatchRecord(
            bot_id="bot_test",
            entity_id="100000",
            patch_id=patch_id,
            layer=Layer.L1,
            target=PatchTarget(files=[]),
            status=PatchStatus.APPLIED,
        )
        applied_record.id = 1

        with patch.object(type(patch_record_repo), "get_by_patch_id", return_value=None), \
             patch.object(type(patch_record_repo), "create", return_value=1), \
             patch.object(
                type(mock_engine), "apply", new_callable=AsyncMock, return_value=applied_record
             ):
            resp = client.post(
                "/api/harness/admin/apply",
                json={
                    "bot_id": "bot_test",
                    "entity_id": "100000",
                    "patch_id_list": [patch_id],
                },
                headers={"x-user-id": "100000"},
            )
        assert resp.status_code == 200
        assert resp.json().get("success") is True