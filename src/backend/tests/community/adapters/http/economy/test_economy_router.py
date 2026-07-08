"""Unit tests for the economy/governance HTTP router.

Follows the ``tests/adapters/http/quality/test_quality_router.py`` pattern:
call the async route handlers *directly* with simple in-memory Fake/Stub
services passed in place of the ``Injected(...)`` defaults. This exercises
every handler branch without standing up the production DI graph, TestClient,
or any network / MOSN / ZDAS dependency.
"""
from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.economy import admin_router, router
from agentclaw.community.adapters.http.economy.schemas import (
    CardCallbackIFrameRequest,
    EmergencyRequest,
    GovernanceNotifyResolveRequest,
    OfflineBatchRequest,
    WhitelistBatchRequest,
    WhitelistEntry,
)


def _run(coro):
    """Run an async route handler from a sync test."""
    return asyncio.run(coro)


def _ctx(user_id: str = "88888") -> RequestContext:
    return RequestContext(user_id=user_id, bot_id="default", nick_name="tester")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ResolveResult:
    """Stand-in for the core ResolveResult returned by ``resolve()``."""

    def __init__(
        self,
        *,
        success: bool = True,
        notification_id: str = "n-1",
        governance_status: str = "closed",
        close_reason: str | None = "optimized",
        mute_until: Any = None,
        error: str | None = None,
        error_code: str | None = None,
        response: str = "optimized",
        response_source: str = "http_api",
        message: str | None = None,
    ) -> None:
        self.success = success
        self.notification_id = notification_id
        self.governance_status = governance_status
        self.close_reason = close_reason
        self.mute_until = mute_until
        self.error = error
        self.error_code = error_code
        self.response = response
        self.response_source = response_source
        self.message = message


class FakeFeedbackService:
    """In-memory fake of GovernanceFeedbackServiceProtocol."""

    def __init__(self) -> None:
        self.pending: list[dict] = []
        self.history: list[dict] = []
        self.notifications: dict[str, dict] = {}
        self.resolve_result = _ResolveResult()
        self.resolve_calls: list[dict] = []

    def list_pending(self, owner_id: str, *, limit: int, offset: int) -> list[dict]:
        return self.pending

    def list_history(self, owner_id: str, *, limit: int, offset: int) -> list[dict]:
        return self.history

    def get_notification(self, notification_id: str, owner_id: str) -> dict | None:
        return self.notifications.get(notification_id)

    def resolve(self, **kwargs: Any) -> _ResolveResult:
        self.resolve_calls.append(kwargs)
        return self.resolve_result


class FakeWhitelistService:
    """In-memory fake of GovernanceWhitelistRepository (as used by router)."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.batch_result = {"inserted": 0, "skipped": 0}

    def batch_add(self, *, entries, created_by, whitelist_type, source) -> dict:
        self.entries.extend(entries)
        return {"inserted": len(entries), "skipped": 0}

    def list_all(self, *, owner_id, whitelist_type, limit, offset) -> list[dict]:
        return self.entries


class FakeAdminService:
    """In-memory fake of GovernanceAdminServiceProtocol."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.state = {
            "paused": False,
            "reason": None,
            "operator": None,
            "paused_at": None,
            "pending_count": 0,
            "open_count": 0,
            "whitelist_count": 0,
        }
        self._deliver_result: dict = {"total": 0, "dry_run": True, "results": [], "sent_count": 0}
        self.deliver_calls: list[dict] = []

    def is_paused(self) -> bool:
        return self.state["paused"]

    def get_state(self) -> dict:
        return self.state

    def pause(self, reason: str, operator: str) -> None:
        self.calls.append("pause")
        self.state["paused"] = True

    def resume(self, reason: str, operator: str) -> None:
        self.calls.append("resume")
        self.state["paused"] = False

    def bulk_whitelist(self, bot_ids, reason, operator) -> dict:
        self.calls.append("bulk_whitelist")
        return {"whitelisted": len(bot_ids)}

    def cancel_pending(self, reason, operator) -> dict:
        self.calls.append("cancel_pending")
        return {"cancelled": 3}

    def close_all_open(self, reason, operator) -> dict:
        self.calls.append("close_all_open")
        return {"closed": 5}

    async def deliver_pending(
        self,
        *,
        scan_svc: Any,
        override_recipient: str,
        dry_run: bool,
        max_send: int,
        channel: str,
        skip_scan: bool,
        scan_dry_run: bool,
    ) -> dict:
        """Fake of GovernanceAdminServiceProtocol.deliver_pending."""
        self.deliver_calls.append({
            "override_recipient": override_recipient,
            "dry_run": dry_run,
            "max_send": max_send,
            "channel": channel,
            "skip_scan": skip_scan,
            "scan_dry_run": scan_dry_run,
        })
        return dict(self._deliver_result)


class FakeBatchService:
    """In-memory fake of GovernanceRecordProcessProtocol."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def batch_upsert_task_recs(self, records, env=None) -> dict:
        self.calls.append({"records": records, "env": env})
        return {"inserted": len(records), "updated": 0, "errors": 0}

    def process_offline_batch(
        self,
        records: list[dict],
        *,
        batch_id: str = "",
        dt_version: str = "",
        total_count: int = 0,
        dry_run: bool = False,
    ) -> _OfflineBatchResult:
        """Fake of GovernanceRecordProcessProtocol.process_offline_batch."""
        self.calls.append({
            "records": records,
            "batch_id": batch_id,
            "dt_version": dt_version,
            "total_count": total_count,
            "dry_run": dry_run,
        })
        return _OfflineBatchResult(
            batch_id=batch_id,
            total_records=len(records),
        )


class _ScanSummary:
    """Legacy summary for process_run (kept for backward compat if needed)."""

    def __init__(self) -> None:
        self.run_id = "run-1"
        self.dt_version = "20260701"
        self.total_actionable = 2
        self.newly_enqueued = 1
        self.whitelist_filtered = 0
        self.muted = 0
        self.cooldown_filtered = 0
        self.auto_resolved = 0
        self.data_not_ready = False
        self.errors = 0
        self.dry_run = True
        self.duration_seconds = 0.1


class _CronTickSummary:
    """Fake CronTickSummary matching _cron_tick_to_dict expected fields."""

    def __init__(self) -> None:
        self.run_id = "run-1"
        self.sent_count = 0
        self.failed_count = 0
        self.cancelled_count = 0
        self.reminders_created = 0
        self.schedule_due_count = 0
        self.timeout_recovered = 0
        self.auto_silence_closed = 0
        self.errors = 0
        self.dry_run = True
        self.duration_seconds = 0.1


class _OfflineBatchResult:
    """Fake OfflineBatchResult for FakeBatchService.process_offline_batch."""

    def __init__(
        self,
        *,
        batch_id: str = "b-1",
        run_id: str = "run-1",
        total_records: int = 0,
        upsert_results: list | None = None,
        batch_quality_skipped: bool = False,
        batch_quality_skip_reasons: list | None = None,
        errors: int = 0,
    ) -> None:
        self.batch_id = batch_id
        self.run_id = run_id
        self.total_records = total_records
        self.upsert_results = upsert_results or []
        self.batch_quality_skipped = batch_quality_skipped
        self.batch_quality_skip_reasons = batch_quality_skip_reasons or []
        self.errors = errors


class FakeScanService:
    """Async fake of GovernanceBotServiceProtocol."""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def process_cron_tick(self, *, dry_run=None):
        """Fake of GovernanceBotServiceProtocol.process_cron_tick."""
        self.calls.append({"method": "process_cron_tick", "dry_run": dry_run})
        if self.raise_exc:
            raise self.raise_exc
        return _CronTickSummary()

    async def process_run(self, *, dry_run=None, skip_delivery=False, notify_source="cron"):
        """Fake of GovernanceBotServiceProtocol.process_run."""
        self.calls.append(
            {"method": "process_run", "dry_run": dry_run, "skip_delivery": skip_delivery, "notify_source": notify_source}
        )
        if self.raise_exc:
            raise self.raise_exc
        return _CronTickSummary()


# ---- scan_and_deliver plumbing fakes ------------------------------------


class _FakeRow:
    """A single fake GovernanceNotifyLog row (attribute bag)."""

    def __init__(self, **kw: Any) -> None:
        self.notification_id = kw.get("notification_id", "n-1")
        self.bot_id = kw.get("bot_id", "bot-1")
        self.bot_name = kw.get("bot_name", "Bot One")
        self.owner_id = kw.get("owner_id", "12345")
        self.dt_version = kw.get("dt_version", "20260701")
        self.hit_dimensions = kw.get("hit_dimensions", "token")
        self.governance_max_priority = kw.get("governance_max_priority", "P1")
        self.expected_token_saving = kw.get("expected_token_saving", 100)
        self.saving_ratio = kw.get("saving_ratio", 0.5)
        self.notification_md = kw.get("notification_md", "# md body")
        self.notification_structured = kw.get("notification_structured", None)
        self.notify_channel = kw.get("notify_channel", "markdown")
        self.send_attempt_count = kw.get("send_attempt_count", 0)
        self.notify_status = "pending"
        self.sent_at = None
        self.external_message_id = None


class _FakeQuery:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    def filter(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def all(self) -> list[_FakeRow]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows
        self.committed = False

    def query(self, *args, **kwargs) -> _FakeQuery:
        return _FakeQuery(self._rows)

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class FakeDatabasePlugin:
    def __init__(self, rows: list[_FakeRow] | None = None) -> None:
        self._rows = rows or []

    def orm_session(self) -> _FakeSession:
        return _FakeSession(self._rows)


class FakeNotifySender:
    def __init__(self, *, markdown_id="md-1", tc_card_id="tc-1") -> None:
        self._markdown_id = markdown_id
        self._tc_card_id = tc_card_id
        self.markdown_calls: list[dict] = []
        self.tc_card_calls: list[dict] = []

    def send_markdown(self, *, user_id, title, content):
        self.markdown_calls.append({"user_id": user_id, "title": title})
        return self._markdown_id

    def send_tc_card(self, **kwargs):
        self.tc_card_calls.append(kwargs)
        return self._tc_card_id


class FakeNotifyRepo:
    def __init__(self) -> None:
        self.audits: list[dict] = []

    def add_audit(self, session, run_id, bot_id, owner_id, **kwargs):
        self.audits.append({"run_id": run_id, "bot_id": bot_id, **kwargs})


def _dingtalk_config() -> types.SimpleNamespace:
    return types.SimpleNamespace(iframe_callback_url="https://cb.example/iframe")


def _gov_config() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        tc_card_id="card-xyz",
        tc_card_preview_url="https://preview.test/preview",
    )


# ===========================================================================
# Public endpoints
# ===========================================================================


class TestListNotifications:
    def test_list_pending(self):
        svc = FakeFeedbackService()
        svc.pending = [{"id": "a"}, {"id": "b"}]
        resp = _run(router.list_pending_notifications(ctx=_ctx(), feedback_svc=svc, limit=50, offset=0))
        assert resp.success is True
        assert resp.data == [{"id": "a"}, {"id": "b"}]

    def test_list_history(self):
        svc = FakeFeedbackService()
        svc.history = [{"id": "c"}]
        resp = _run(router.list_history_notifications(ctx=_ctx(), feedback_svc=svc, limit=10, offset=0))
        assert resp.success is True
        assert resp.data == [{"id": "c"}]


class TestGetNotificationDetail:
    def test_found(self):
        svc = FakeFeedbackService()
        svc.notifications["n-9"] = {"id": "n-9", "status": "open"}
        resp = _run(router.get_notification_detail(notification_id="n-9", ctx=_ctx(), feedback_svc=svc))
        assert resp.success is True
        assert resp.data["id"] == "n-9"

    def test_not_found_404(self):
        svc = FakeFeedbackService()
        with pytest.raises(HTTPException) as exc:
            _run(router.get_notification_detail(notification_id="missing", ctx=_ctx(), feedback_svc=svc))
        assert exc.value.status_code == 404


class TestResolveNotification:
    def test_success(self):
        svc = FakeFeedbackService()
        body = GovernanceNotifyResolveRequest(response="optimized")
        resp = _run(router.resolve_notification(notification_id="n-1", body=body, ctx=_ctx(), feedback_svc=svc))
        assert resp.success is True
        assert resp.data["notification_id"] == "n-1"
        assert resp.data["governance_status"] == "closed"

    def test_success_with_mute_until(self):
        from datetime import datetime

        svc = FakeFeedbackService()
        svc.resolve_result = _ResolveResult(
            governance_status="muted",
            close_reason=None,
            mute_until=datetime(2026, 7, 1, 12, 0, 0),
        )
        body = GovernanceNotifyResolveRequest(response="need_time", repair_deadline="2026-07-10")
        resp = _run(router.resolve_notification(notification_id="n-1", body=body, ctx=_ctx(), feedback_svc=svc))
        assert resp.data["mute_until"] == "2026-07-01T12:00:00"
        # repair_deadline was parsed & forwarded
        assert svc.resolve_calls[0]["repair_deadline"] is not None

    def test_bad_repair_deadline_400(self):
        svc = FakeFeedbackService()
        body = GovernanceNotifyResolveRequest(response="need_time", repair_deadline="not-a-date")
        with pytest.raises(HTTPException) as exc:
            _run(router.resolve_notification(notification_id="n-1", body=body, ctx=_ctx(), feedback_svc=svc))
        assert exc.value.status_code == 400
        assert "repair_deadline" in exc.value.detail

    def test_resolve_not_found_404(self):
        svc = FakeFeedbackService()
        svc.resolve_result = _ResolveResult(success=False, error="nope", error_code="NOT_FOUND")
        body = GovernanceNotifyResolveRequest(response="optimized")
        with pytest.raises(HTTPException) as exc:
            _run(router.resolve_notification(notification_id="n-1", body=body, ctx=_ctx(), feedback_svc=svc))
        assert exc.value.status_code == 404

    def test_resolve_generic_error_400(self):
        svc = FakeFeedbackService()
        svc.resolve_result = _ResolveResult(success=False, error="bad", error_code="INVALID_RESPONSE")
        body = GovernanceNotifyResolveRequest(response="optimized")
        with pytest.raises(HTTPException) as exc:
            _run(router.resolve_notification(notification_id="n-1", body=body, ctx=_ctx(), feedback_svc=svc))
        assert exc.value.status_code == 400


class TestWhitelist:
    def test_batch_whitelist(self):
        svc = FakeWhitelistService()
        body = WhitelistBatchRequest(
            entries=[WhitelistEntry(bot_id="b1", owner_id="o1"), WhitelistEntry(bot_id="b2", owner_id="o2")],
            source="admin",
        )
        resp = _run(router.batch_whitelist(body=body, ctx=_ctx(), whitelist_svc=svc))
        assert resp.success is True
        assert resp.data["inserted"] == 2

    def test_list_whitelist(self):
        svc = FakeWhitelistService()
        svc.entries = [{"bot_id": "b1"}]
        resp = _run(router.list_whitelist(ctx=_ctx(), whitelist_svc=svc, limit=100, offset=0))
        assert resp.success is True
        assert resp.data == [{"bot_id": "b1"}]


class TestCardCallback:
    def test_success(self):
        svc = FakeFeedbackService()
        body = CardCallbackIFrameRequest(notification_id="n-1", response="optimized")
        resp = _run(router.card_callback(body=body, feedback_svc=svc))
        assert resp.success is True
        assert resp.data["notification_id"] == "n-1"
        assert svc.resolve_calls[0]["source"] == "card_callback"

    def test_ignores_bad_repair_deadline(self):
        # card_callback swallows a bad repair_deadline (passes None) rather than 400.
        svc = FakeFeedbackService()
        body = CardCallbackIFrameRequest(
            notification_id="n-1", response="need_time", repair_deadline="garbage"
        )
        resp = _run(router.card_callback(body=body, feedback_svc=svc))
        assert resp.success is True
        assert svc.resolve_calls[0]["repair_deadline"] is None

    def test_error_maps_status(self):
        svc = FakeFeedbackService()
        svc.resolve_result = _ResolveResult(success=False, error="missing", error_code="MISSING_REMARK")
        body = CardCallbackIFrameRequest(notification_id="n-1", response="dispute")
        with pytest.raises(HTTPException) as exc:
            _run(router.card_callback(body=body, feedback_svc=svc))
        assert exc.value.status_code == 400

    def test_error_unknown_code_defaults_400(self):
        svc = FakeFeedbackService()
        svc.resolve_result = _ResolveResult(success=False, error="weird", error_code="SOMETHING_ELSE")
        body = CardCallbackIFrameRequest(notification_id="n-1", response="dispute")
        with pytest.raises(HTTPException) as exc:
            _run(router.card_callback(body=body, feedback_svc=svc))
        assert exc.value.status_code == 400


# ===========================================================================
# Internal endpoints
# ===========================================================================


class TestOfflineBatch:
    def test_offline_batch(self):
        svc = FakeBatchService()
        body = OfflineBatchRequest(
            records=[{"worker_key": "u1:b1"}, {"worker_key": "u2:b2"}],
            batch_id="b-test",
            dt_version="20260705",
            total_count=2,
        )
        resp = _run(router.offline_batch(body=body, partial_svc=svc))
        assert resp.success is True
        assert resp.data["batch_id"] == "b-test"
        assert resp.data["total_records"] == 2
        assert svc.calls[0]["batch_id"] == "b-test"


class TestTriggerScan:
    def test_success(self):
        svc = FakeScanService()
        resp = _run(admin_router.trigger_scan(ctx=_ctx(), dry_run=False, scan_svc=svc))
        assert resp.success is True
        assert resp.data["run_id"] == "run-1"
        assert svc.calls[0]["dry_run"] is False
        assert svc.calls[0]["method"] == "process_cron_tick"

    def test_exception_returns_error_shape(self):
        svc = FakeScanService(raise_exc=RuntimeError("boom"))
        resp = _run(admin_router.trigger_scan(ctx=_ctx(), dry_run=None, scan_svc=svc))
        assert resp.success is False
        assert resp.error_code == "SCAN_ERROR"


class TestEmergencyAction:
    def _call(self, body: EmergencyRequest, admin: FakeAdminService):
        return _run(admin_router.emergency_action(body=body, ctx=None, admin_svc=admin))

    def test_pause(self):
        admin = FakeAdminService()
        resp = self._call(EmergencyRequest(action="pause", reason="r"), admin)
        assert resp.success is True and resp.message == "Paused"
        assert "pause" in admin.calls

    def test_resume(self):
        admin = FakeAdminService()
        resp = self._call(EmergencyRequest(action="resume", reason="r"), admin)
        assert resp.message == "Resumed"
        assert "resume" in admin.calls

    def test_bulk_whitelist(self):
        admin = FakeAdminService()
        resp = self._call(EmergencyRequest(action="bulk-whitelist", reason="r", bot_ids=["b1", "b2"]), admin)
        assert resp.success is True
        assert resp.data == {"whitelisted": 2}

    def test_bulk_whitelist_missing_bot_ids_400(self):
        admin = FakeAdminService()
        with pytest.raises(HTTPException) as exc:
            self._call(EmergencyRequest(action="bulk-whitelist", reason="r"), admin)
        assert exc.value.status_code == 400

    def test_cancel_pending(self):
        admin = FakeAdminService()
        resp = self._call(EmergencyRequest(action="cancel-pending", reason="r"), admin)
        assert resp.data == {"cancelled": 3}

    def test_close_all_open(self):
        admin = FakeAdminService()
        resp = self._call(EmergencyRequest(action="close-all-open", reason="r"), admin)
        assert resp.data == {"closed": 5}

    def test_unknown_action_400(self):
        admin = FakeAdminService()
        with pytest.raises(HTTPException) as exc:
            self._call(EmergencyRequest(action="nuke", reason="r"), admin)
        assert exc.value.status_code == 400
        assert "Unknown action" in exc.value.detail


class TestEmergencyState:
    def test_get_state(self):
        admin = FakeAdminService()
        admin.state["pending_count"] = 7
        resp = _run(admin_router.get_emergency_state(ctx=_ctx(), admin_svc=admin))
        assert resp.success is True
        assert resp.data["pending_count"] == 7
        assert resp.data["paused"] is False


# ===========================================================================
# scan_and_deliver — the big one
# ===========================================================================


def _deliver_kwargs(*, admin_svc: FakeAdminService | None = None, scan_svc: FakeScanService | None = None, **overrides: Any) -> dict:
    """Assemble the full kwargs bag for scan_and_deliver, with sane defaults.

    The new scan_and_deliver signature only takes: ctx, override_recipient,
    dry_run, max_send, skip_scan, scan_dry_run, channel, scan_svc, admin_svc.
    Delivery logic is delegated to admin_svc.deliver_pending().
    """
    base = dict(
        ctx=None,
        override_recipient="123456",
        dry_run=True,
        max_send=0,
        skip_scan=True,
        scan_dry_run=False,
        channel="auto",
        scan_svc=scan_svc or FakeScanService(),
        admin_svc=admin_svc or FakeAdminService(),
    )
    base.update(overrides)
    return base


class TestScanAndDeliver:
    def test_no_pending_no_scan(self):
        admin = FakeAdminService()
        # deliver_pending returns total=0 by default, skip_scan=True → no scan_summary
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(admin_svc=admin)))
        assert resp.success is True
        assert resp.message == "No pending notifications to deliver"
        assert resp.data["total"] == 0

    def test_deliver_pending_called_with_correct_kwargs(self):
        admin = FakeAdminService()
        admin._deliver_result = {
            "total": 2,
            "dry_run": True,
            "results": [
                {"notification_id": "n-1", "channel": "markdown"},
                {"notification_id": "n-2", "channel": "tc_card"},
            ],
            "sent_count": 0,
        }
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            admin_svc=admin, dry_run=True, channel="auto", max_send=5,
        )))
        assert resp.success is True
        assert resp.data["total"] == 2
        assert admin.deliver_calls[0]["dry_run"] is True
        assert admin.deliver_calls[0]["channel"] == "auto"
        assert admin.deliver_calls[0]["max_send"] == 5

    def test_scan_runs_when_not_skipped(self):
        scan = FakeScanService()
        admin = FakeAdminService()
        admin._deliver_result = {"total": 1, "dry_run": True, "results": [], "sent_count": 0}
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            skip_scan=False, scan_svc=scan, admin_svc=admin,
        )))
        assert resp.data["scan"]["run_id"] == "run-1"
        assert scan.calls[0]["dry_run"] is False
        assert scan.calls[0]["method"] == "process_cron_tick"

    def test_scan_failure_is_captured(self):
        scan = FakeScanService(raise_exc=RuntimeError("scan boom"))
        admin = FakeAdminService()
        admin._deliver_result = {"total": 1, "dry_run": True, "results": [], "sent_count": 0}
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            skip_scan=False, scan_svc=scan, admin_svc=admin,
        )))
        assert resp.data["scan"]["error"].startswith("Cron tick failed")

    def test_max_send_passed_through(self):
        admin = FakeAdminService()
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            admin_svc=admin, max_send=2,
        )))
        assert admin.deliver_calls[0]["max_send"] == 2

    def test_live_requires_numeric_recipient(self):
        # dry_run=False with a non-digit recipient → 400 guard.
        # (regex normally blocks this at param level; here we call the handler directly.)
        with pytest.raises(HTTPException) as exc:
            _run(admin_router.scan_and_deliver(**_deliver_kwargs(
                admin_svc=FakeAdminService(), dry_run=False, override_recipient="abc",
            )))
        assert exc.value.status_code == 400

    def test_channel_passed_through(self):
        admin = FakeAdminService()
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            admin_svc=admin, channel="tc_card",
        )))
        assert admin.deliver_calls[0]["channel"] == "tc_card"

    def test_scan_summary_merged_into_response(self):
        scan = FakeScanService()
        admin = FakeAdminService()
        admin._deliver_result = {"total": 1, "dry_run": True, "results": [], "sent_count": 0}
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            skip_scan=False, scan_svc=scan, admin_svc=admin,
        )))
        assert "scan" in resp.data
        assert resp.data["scan"]["run_id"] == "run-1"

    def test_no_pending_with_scan_summary_no_message(self):
        admin = FakeAdminService()
        # total=0 but scan ran → has scan_summary → no "No pending" message
        scan = FakeScanService()
        resp = _run(admin_router.scan_and_deliver(**_deliver_kwargs(
            skip_scan=False, scan_svc=scan, admin_svc=admin,
        )))
        assert resp.success is True
        assert resp.message != "No pending notifications to deliver"  # no special message when scan_summary exists
        assert resp.data["scan"]["run_id"] == "run-1"
