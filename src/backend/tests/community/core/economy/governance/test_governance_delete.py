"""Unit tests for governance emergency delete endpoint.

Covers router-level logic:
  - POST /admin/records/delete
    - record_daily: dt_versions filter, ids filter, dry_run, real delete
    - notify_log: notification_ids filter, dry_run, real delete
    - validation: no filter → 400, bad table → 400

Repo-level session-passing tests have been removed — session management
is now an internal implementation detail of self-managed orm_session.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.economy import admin_router
from agentclaw.community.adapters.http.economy.schemas import (
    RecordsDeleteRequest,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)


def _run(coro):
    """Run an async route handler from a sync test."""
    return asyncio.run(coro)


def _ctx(user_id: str = "88888") -> RequestContext:
    return RequestContext(user_id=user_id, bot_id="default", nick_name="tester")


# ---------------------------------------------------------------------------
# Fakes for delete endpoint
# ---------------------------------------------------------------------------


class FakeTaskRecordRepo:
    """In-memory fake of TaskRecordRepository for delete operations."""

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._deleted_dt: list[list[str]] = []
        self._deleted_ids: list[list[int]] = []

    def seed(self, rows: list[dict]) -> None:
        self._rows = rows

    def count_by_dt_versions(self, dt_versions: list[str], **kw: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        for dt in dt_versions:
            counts[dt] = sum(1 for r in self._rows if r["dt_version"] == dt)
        return counts

    def delete_by_dt_versions(self, dt_versions: list[str], **kw: Any) -> int:
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["dt_version"] not in dt_versions]
        self._deleted_dt.append(dt_versions)
        return before - len(self._rows)

    def count_by_ids(self, ids: list[int], **kw: Any) -> tuple[int, list[int]]:
        existing = {r["id"] for r in self._rows if r["id"] in ids}
        not_found = [i for i in ids if i not in existing]
        return len(existing), not_found

    def delete_by_ids(self, ids: list[int], **kw: Any) -> tuple[int, list[int]]:
        before = len(self._rows)
        existing_ids = {r["id"] for r in self._rows if r["id"] in ids}
        not_found = [i for i in ids if i not in existing_ids]
        self._rows = [r for r in self._rows if r["id"] not in ids]
        self._deleted_ids.append(ids)
        return before - len(self._rows), not_found


class FakeNotifyLogRepo:
    """In-memory fake of NotifyLogRepository for delete operations."""

    def __init__(self) -> None:
        self.audits: list[dict] = []
        self._rows: list[dict] = []

    def seed(self, rows: list[dict]) -> None:
        self._rows = rows

    def add_audit(self, run_id: str, **kwargs: Any) -> None:
        self.audits.append({"run_id": run_id, **kwargs})

    def count_by_notification_ids(
        self, notification_ids: list[str], **kw: Any,
    ) -> tuple[int, list[str]]:
        existing = {r["notification_id"] for r in self._rows if r["notification_id"] in notification_ids}
        not_found = [i for i in notification_ids if i not in existing]
        return len(existing), not_found

    def delete_by_notification_ids(
        self, notification_ids: list[str], **kw: Any,
    ) -> tuple[int, list[str]]:
        existing = {r["notification_id"] for r in self._rows if r["notification_id"] in notification_ids}
        not_found = [i for i in notification_ids if i not in existing]
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["notification_id"] not in notification_ids]
        return before - len(self._rows), not_found


class FakeAuditRepo:
    """In-memory fake of GovernanceAuditRepository."""

    def __init__(self) -> None:
        self.audits: list[dict] = []

    def add_audit(self, run_id: str, **kwargs: Any) -> None:
        self.audits.append({"run_id": run_id, **kwargs})


class _FakeSession:
    def commit(self) -> None:
        pass

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeDatabasePlugin:
    def orm_session(self) -> _FakeSession:
        return _FakeSession()


class FakeWhitelistRepo:
    """In-memory fake of GovernanceWhitelistRepository for delete operations."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    def seed(self, rows: list[dict]) -> None:
        self._rows = rows

    def is_whitelisted(self, bot_id, owner_id, **kwargs):
        return any(
            r.get("bot_id") == bot_id and r.get("owner_id") == owner_id
            for r in self._rows
        )

    def add(self, *, bot_id, owner_id, created_by, **kwargs):
        from agentclaw.community.core.economy.governance.domain.whitelist import WhitelistEntry
        self._rows.append({"bot_id": bot_id, "owner_id": owner_id})
        return WhitelistEntry(
            bot_id=bot_id, owner_id=owner_id,
            whitelist_type=kwargs.get("whitelist_type", "governance"),
            source=kwargs.get("source", "manual"),
            reason=kwargs.get("reason", ""),
            created_by=created_by, expires_at=None,
        )

    def remove(self, *, bot_id, owner_id, whitelist_type="governance"):
        for i, row in enumerate(self._rows):
            if row.get("bot_id") == bot_id and row.get("owner_id") == owner_id:
                self._rows.pop(i)
                return True
        return False

    def list_by_owner(self, owner_id, **kwargs):
        return []

    def count_by_type(self, **kwargs):
        return 0


class FakeGovernanceConfig:
    """Minimal EconomyGovernanceConfig for delete operations."""
    dry_run: bool = False
    skip_weekends: bool = False
    cooldown_days: int = 14
    auto_silence_close_days: int = 7
    notify_channel: str = "markdown"
    tc_card_id: str = "card_cb190863"
    tc_card_template_id: str = ""


class FakeAdminService:
    """Delegates delete_records to real GovernanceAdminService logic
    backed by in-memory fakes.

    This lets the router-level tests exercise the full service path
    (router → admin_svc.delete_records) without a real database.
    """

    def __init__(
        self,
        task_repo: FakeTaskRecordRepo | None = None,
        notify_repo: FakeNotifyLogRepo | None = None,
        whitelist_repo: FakeWhitelistRepo | None = None,
    ) -> None:
        from agentclaw.community.core.economy.governance.services.admin_service import (
            GovernanceAdminService,
        )

        from agentclaw.community.core.economy.governance.services.lifecycle_service import (
            GovernanceLifecycleService,
        )
        from agentclaw.community.core.economy.governance.services.whitelist_service import (
            GovernanceWhitelistService,
        )

        self._task_repo = task_repo or FakeTaskRecordRepo()
        self._notify_repo = notify_repo or FakeNotifyLogRepo()
        self._audit_repo = FakeAuditRepo()
        self._whitelist_repo = whitelist_repo or FakeWhitelistRepo()
        self._db = FakeDatabasePlugin()

        # Build the driver — lifecycle_service has no whitelist dependency
        # (accept_feedback whitelist-add is owned by feedback_service), so no
        # stub needed. delete_records / delete_whitelist_entry do not
        # exercise the timer state machine or bulk_whitelist.
        self._lifecycle_svc = GovernanceLifecycleService(
            task_repo=self._task_repo,  # type: ignore[arg-type]
            notify_repo=self._notify_repo,  # type: ignore[arg-type]
            audit_repo=self._audit_repo,
        )

        # Build whitelist_service with proper in-memory fake whitelist_repo.
        # It needs lifecycle_svc (Task 8 bulk_whitelist closes task_record);
        # not invoked by delete tests, so the driver above suffices.
        self._whitelist_service = GovernanceWhitelistService(
            whitelist_repo=self._whitelist_repo,  # type: ignore[arg-type]
            notify_repo=self._notify_repo,  # type: ignore[arg-type]
            audit_repo=self._audit_repo,
            config=FakeGovernanceConfig(),  # type: ignore[arg-type]
            lifecycle_svc=self._lifecycle_svc,
        )

        self._real_svc = GovernanceAdminService(
            cache=None,  # type: ignore[arg-type]
            whitelist_service=self._whitelist_service,
            notify_repo=self._notify_repo,  # type: ignore[arg-type]
            audit_repo=self._audit_repo,
            task_repo=self._task_repo,  # type: ignore[arg-type]
            config=FakeGovernanceConfig(),  # type: ignore[arg-type]
            notify_sender=None,  # type: ignore[arg-type]
            lifecycle_svc=self._lifecycle_svc,
        render_svc=NotifyRenderService(),
        )

    def delete_records(self, body: dict, operator: str) -> dict:
        return self._real_svc.delete_records(body, operator)


# ===========================================================================
# Router-level tests
# ===========================================================================


class TestDeleteEndpointRecordDaily:
    """Tests for DELETE /admin/records/delete with table=record_daily."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_dry_run_dt_versions_counts_only(self):
        repo = FakeTaskRecordRepo()
        repo.seed([
            {"id": 1, "dt_version": "20260622"},
            {"id": 2, "dt_version": "20260622"},
            {"id": 3, "dt_version": "20260623"},
        ])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260622"],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.success is True
        assert resp.data["would_delete"] == 2
        assert resp.data["deleted"] == 0
        assert resp.data["dry_run"] is True
        assert len(repo._rows) == 3

    def test_real_delete_dt_versions(self):
        repo = FakeTaskRecordRepo()
        repo.seed([
            {"id": 1, "dt_version": "20260622"},
            {"id": 2, "dt_version": "20260622"},
            {"id": 3, "dt_version": "20260623"},
        ])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260622"],
            dry_run=False,
            reason="cleanup",
        )
        admin_svc = FakeAdminService(task_repo=repo)
        resp = _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))
        assert resp.data["deleted"] == 2
        assert resp.data["would_delete"] == 2
        assert len(repo._rows) == 1
        # audit goes to audit_repo, not notify_repo
        assert any(a["action_taken"] == "records_deleted" for a in admin_svc._audit_repo.audits)

    def test_dry_run_ids_counts_with_not_found(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}, {"id": 2, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            ids=[1, 99],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["would_delete"] == 1
        assert 99 in resp.data["not_found"]
        assert len(repo._rows) == 2

    def test_real_delete_ids(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}, {"id": 2, "dt_version": "20260623"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            ids=[1],
            dry_run=False,
            reason="cleanup",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["deleted"] == 1
        assert len(repo._rows) == 1
        assert repo._rows[0]["id"] == 2


class TestDeleteEndpointNotifyLog:
    """Tests for DELETE /admin/records/delete with table=notify_log."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_dry_run_notification_ids(self):
        repo = FakeNotifyLogRepo()
        repo.seed([
            {"notification_id": "n-1"},
            {"notification_id": "n-2"},
        ])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-1", "n-missing"],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.data["would_delete"] == 1
        assert resp.data["deleted"] == 0
        assert "n-missing" in resp.data["not_found"]
        assert len(repo._rows) == 2

    def test_real_delete_notification_ids(self):
        repo = FakeNotifyLogRepo()
        repo.seed([
            {"notification_id": "n-1"},
            {"notification_id": "n-2"},
        ])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-1"],
            dry_run=False,
            reason="cleanup",
        )
        admin_svc = FakeAdminService(notify_repo=repo)
        resp = _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))
        assert resp.data["deleted"] == 1
        assert len(repo._rows) == 1
        assert repo._rows[0]["notification_id"] == "n-2"
        assert any(a["action_taken"] == "notifications_deleted" for a in admin_svc._audit_repo.audits)

    def test_delete_nonexistent_notification_id(self):
        repo = FakeNotifyLogRepo()
        repo.seed([{"notification_id": "n-1"}])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-999"],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.data["would_delete"] == 0
        assert "n-999" in resp.data["not_found"]


class TestDeleteEndpointValidation:
    """Validation and error cases."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_no_filter_returns_400(self):
        body = RecordsDeleteRequest(table="record_daily", reason="test")
        with pytest.raises(HTTPException) as exc:
            self._call(body)
        assert exc.value.status_code == 400
        assert "At least one" in exc.value.detail

    def test_bad_table_returns_400(self):
        body = RecordsDeleteRequest(
            table="bad_table", dt_versions=["20260622"], reason="test",
        )
        with pytest.raises(HTTPException) as exc:
            self._call(body)
        assert exc.value.status_code == 400
        assert "Unknown table" in exc.value.detail

    def test_default_dry_run_is_true(self):
        body = RecordsDeleteRequest(
            table="record_daily", dt_versions=["20260622"], reason="test",
        )
        assert body.dry_run is True


class TestDeleteEndpointRecordDailyCornerCases:
    """Corner cases for record_daily delete path."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_delete_empty_dt_versions_no_rows(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260101"],
            dry_run=False,
            reason="test",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["would_delete"] == 0
        assert resp.data["deleted"] == 0
        assert len(repo._rows) == 1

    def test_dry_run_ids_all_not_found(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            ids=[99, 100],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["would_delete"] == 0
        assert 99 in resp.data["not_found"]
        assert 100 in resp.data["not_found"]

    def test_dry_run_with_only_dt_versions(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260622"],
            dry_run=True,
            reason="preview",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["would_delete"] == 1
        assert resp.data["deleted"] == 0
        assert resp.data["not_found"] == []

    def test_dry_run_with_only_ids(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            ids=[1],
            dry_run=True,
            reason="preview",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["would_delete"] == 1
        assert resp.data["deleted"] == 0

    def test_real_delete_combined_dt_and_ids(self):
        repo = FakeTaskRecordRepo()
        repo.seed([
            {"id": 1, "dt_version": "20260622"},
            {"id": 2, "dt_version": "20260622"},
            {"id": 3, "dt_version": "20260623"},
        ])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260622"],
            ids=[3],
            dry_run=False,
            reason="cleanup",
        )
        resp = self._call(body, task_repo=repo)
        assert resp.data["deleted"] == 3
        assert len(repo._rows) == 0

    def test_audit_contains_operator_and_structured_msg(self):
        repo = FakeTaskRecordRepo()
        repo.seed([{"id": 1, "dt_version": "20260622"}])
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=["20260622"],
            dry_run=False,
            reason="stale data",
        )
        admin_svc = FakeAdminService(task_repo=repo)
        _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))
        audit = [a for a in admin_svc._audit_repo.audits if a["action_taken"] == "records_deleted"]
        assert len(audit) == 1
        assert audit[0]["actor_id"] == "88888"
        assert audit[0]["dry_run"] == 0
        error_msg = audit[0].get("error_msg") or ""
        assert "reason=stale data" in error_msg
        assert "table=record_daily" in error_msg
        assert "deleted=" in error_msg


class TestDeleteEndpointNotifyLogCornerCases:
    """Corner cases for notify_log delete path."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_dry_run_empty_result(self):
        repo = FakeNotifyLogRepo()
        repo.seed([{"notification_id": "n-1"}])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-missing"],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.data["would_delete"] == 0
        assert resp.data["deleted"] == 0

    def test_real_delete_all_not_found(self):
        repo = FakeNotifyLogRepo()
        repo.seed([{"notification_id": "n-1"}])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-x"],
            dry_run=False,
            reason="test",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.data["deleted"] == 0
        assert "n-x" in resp.data["not_found"]

    def test_real_delete_multiple_notification_ids(self):
        repo = FakeNotifyLogRepo()
        repo.seed([
            {"notification_id": "n-1"},
            {"notification_id": "n-2"},
            {"notification_id": "n-3"},
        ])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-1", "n-2", "n-missing"],
            dry_run=False,
            reason="cleanup",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.data["deleted"] == 2
        assert "n-missing" in resp.data["not_found"]
        assert len(repo._rows) == 1


class TestDeleteEndpointValidationCornerCases:
    """Additional validation corner cases."""

    def _call(self, body: RecordsDeleteRequest, **overrides: Any) -> Any:
        task_repo = overrides.pop("task_repo", FakeTaskRecordRepo())
        notify_repo = overrides.pop("notify_repo", FakeNotifyLogRepo())
        admin_svc = FakeAdminService(task_repo=task_repo, notify_repo=notify_repo)
        return _run(admin_router.delete_records(
            body=body, ctx=_ctx(),
            admin_svc=admin_svc,
        ))

    def test_empty_lists_count_as_no_filter(self):
        body = RecordsDeleteRequest(
            table="record_daily",
            dt_versions=[],
            ids=[],
            notification_ids=[],
            reason="test",
        )
        with pytest.raises(HTTPException) as exc:
            self._call(body)
        assert exc.value.status_code == 400

    def test_record_daily_table_name_exact(self):
        body = RecordsDeleteRequest(
            table="Record_Daily",
            dt_versions=["20260622"],
            reason="test",
        )
        with pytest.raises(HTTPException) as exc:
            self._call(body)
        assert exc.value.status_code == 400

    def test_notify_log_table_accepted(self):
        repo = FakeNotifyLogRepo()
        repo.seed([{"notification_id": "n-1"}])
        body = RecordsDeleteRequest(
            table="notify_log",
            notification_ids=["n-1"],
            dry_run=True,
            reason="test",
        )
        resp = self._call(body, notify_repo=repo)
        assert resp.success is True