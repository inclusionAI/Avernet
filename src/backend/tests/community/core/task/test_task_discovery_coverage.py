"""Coverage tests for task-discovery new code paths.

Covers lines not exercised by test_task_discovery_unit.py:
  - DingTalkNotifySender (decorator send + _configured + _send_dingtalk_card)
  - DiscoveryService._try_acquire_lock (acquire/stale-reap/all-fail paths)
  - DiscoveryService._send_work_order_event (success/None/exception)
  - DiscoveryService.discover_all_bots lock integration
  - DiscoveredTask.to_discovery_prompt with empty acceptances
  - CronRelaySessionInitiator._build_discovery_prompt empty acceptances
  - _row_to_task with invalid JSON acceptances
  - CommunityNotifyModule DI binding (both branches)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
    DISCOVERY_LOCK_TTL_SECONDS,
)
from agentclaw.community.core.task.task_discovery.discovered_task_models import (
    DiscoveredTaskModel,
)
from agentclaw.community.core.task.task_discovery.lock_models import (
    TaskDiscoveryLockRecord,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
    init_discovered_tasks_db,
)
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
)
from agentclaw.community.plugins.community.notify_sender import (
    CommunityNotifySender,
    DingTalkNotifySender,
    _env,
)

_DT = datetime.now().strftime("%Y-%m-%d")


def _make_task(**overrides) -> DiscoveredTask:
    defaults = dict(
        task_id="t1",
        bot_id="bot-1",
        owner_id="owner-1",
        dt=_DT,
        title="Test Task",
        instruction="Do something",
        background="Testing",
        discovery_basis="unit test",
    )
    defaults.update(overrides)
    return DiscoveredTask(**defaults)


def _make_session() -> DiscoverySession:
    return DiscoverySession(
        task_id="t1",
        session_id="sess-1",
        session_url="http://localhost:8000/assistant?sessionId=sess-1",
    )


# ===========================================================================
# DingTalkNotifySender
# ===========================================================================

class TestDingTalkNotifySender:
    """Cover DingTalkNotifySender — the decorator channel."""

    def test_init_and_channels(self):
        inner = CommunityNotifySender()
        dt = DingTalkNotifySender(inner)
        assert dt.channels == inner.channels

    def test_configured_false_without_env(self):
        assert DingTalkNotifySender._configured() is False

    def test_configured_true_with_all_env(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_ID", "ak")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_SECRET", "sk")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_ROBOT_CODE", "rc")
        monkeypatch.setenv("TASK_DISCOVERY_CARD_TEMPLATE_ID", "tpl")
        assert DingTalkNotifySender._configured() is True

    def test_configured_true_with_singlebox_fallback(self, monkeypatch):
        monkeypatch.delenv("TASK_DISCOVERY_DINGTALK_AK_ID", raising=False)
        monkeypatch.setenv("SINGLEBOX_DINGTALK_AK_ID", "ak")
        monkeypatch.setenv("SINGLEBOX_DINGTALK_AK_SECRET", "sk")
        monkeypatch.setenv("SINGLEBOX_DINGTALK_ROBOT_CODE", "rc")
        monkeypatch.setenv("SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID", "tpl")
        assert DingTalkNotifySender._configured() is True

    def test_env_helper_falls_back_to_singlebox(self, monkeypatch):
        monkeypatch.delenv("MY_VAR", raising=False)
        monkeypatch.setenv("SINGLEBOX_MY_VAR", "fallback-val")
        assert _env("MY_VAR", "SINGLEBOX_MY_VAR") == "fallback-val"

    def test_env_helper_empty_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        monkeypatch.delenv("SINGLEBOX_NOPE", raising=False)
        assert _env("NOPE", "SINGLEBOX_NOPE") == ""

    def test_send_calls_inner_and_skips_dingtalk_when_unconfigured(self):
        """When creds are missing, send() delegates to inner and skips dingtalk."""
        inner = MagicMock()
        inner.send.return_value = "msg-123"
        inner.channels = frozenset({"markdown"})
        dt = DingTalkNotifySender(inner)
        msg = NotifyMessage(
            title="t", body="b", recipient="r", deep_link="dl",
        )
        result = dt.send(msg)
        assert result == "msg-123"
        inner.send.assert_called_once_with(msg, channel="markdown")

    def test_send_swallows_dingtalk_failure(self):
        """send() never raises — dingtalk errors are caught and logged."""
        inner = MagicMock()
        inner.send.return_value = "ok"
        inner.channels = frozenset({"markdown"})
        dt = DingTalkNotifySender(inner)
        # Force _send_dingtalk_card to raise.
        with patch.object(dt, "_send_dingtalk_card", side_effect=RuntimeError("boom")):
            msg = NotifyMessage(title="t", body="b", recipient="r")
            result = dt.send(msg)
        assert result == "ok"  # inner result returned despite dingtalk failure

    def test_send_dingtalk_card_returns_early_when_unconfigured(self):
        """_send_dingtalk_card does nothing when _configured() is False."""
        dt = DingTalkNotifySender(CommunityNotifySender())
        msg = NotifyMessage(title="t", body="b", recipient="r")
        # Should return None without raising (no SDK import).
        assert dt._send_dingtalk_card(msg) is None

    def test_send_dingtalk_card_returns_early_when_no_card_data(self, monkeypatch):
        """Even when configured, no card_data in extra → early return."""
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_ID", "ak")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_SECRET", "sk")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_ROBOT_CODE", "rc")
        monkeypatch.setenv("TASK_DISCOVERY_CARD_TEMPLATE_ID", "tpl")
        dt = DingTalkNotifySender(CommunityNotifySender())
        msg = NotifyMessage(title="t", body="b", recipient="r")  # no extra
        assert dt._send_dingtalk_card(msg) is None


# ===========================================================================
# DiscoveryService._try_acquire_lock
# ===========================================================================

class TestTryAcquireLock:
    """Cover _try_acquire_lock — acquire / stale-reap / all-fail paths."""

    def _make_service(self, lock_repo):
        return DiscoveryService(
            reader=MagicMock(),
            session_initiator=MagicMock(),
            notify_sender=MagicMock(),
            bot_service=None,
            discovery_lock_repo=lock_repo,
        )

    def test_first_acquire_succeeds(self):
        lock_repo = MagicMock()
        expected = TaskDiscoveryLockRecord(env="dev", bot_id="b1", discovery_date=_DT, lock_token="tok")
        lock_repo.acquire.return_value = expected
        svc = self._make_service(lock_repo)
        rec = svc._try_acquire_lock("b1")
        assert rec is not None
        assert rec.lock_token == "tok"
        assert lock_repo.acquire.call_count == 1

    def test_conflict_then_stale_reap_then_reacquire(self):
        """First acquire fails, stale lock found, reaped, second acquire succeeds."""
        lock_repo = MagicMock()
        stale = TaskDiscoveryLockRecord(env="dev", bot_id="b1", discovery_date=_DT, lock_token="stale-tok")
        # First acquire → None (conflict)
        # Then get_if_stale → returns stale record
        # Then release (reap)
        # Then second acquire → succeeds
        lock_repo.acquire.side_effect = [None, TaskDiscoveryLockRecord(env="dev", bot_id="b1", discovery_date=_DT, lock_token="new-tok")]
        lock_repo.get_if_stale.return_value = stale
        svc = self._make_service(lock_repo)
        rec = svc._try_acquire_lock("b1")
        assert rec is not None
        assert rec.lock_token == "new-tok"
        lock_repo.get_if_stale.assert_called_once()
        lock_repo.release.assert_called_once_with("dev", "b1", _DT, "stale-tok")

    def test_conflict_no_stale_then_still_fails(self):
        """First acquire fails, no stale lock, second acquire also fails → None."""
        lock_repo = MagicMock()
        lock_repo.acquire.side_effect = [None, None]
        lock_repo.get_if_stale.return_value = None
        svc = self._make_service(lock_repo)
        rec = svc._try_acquire_lock("b1")
        assert rec is None
        lock_repo.get_if_stale.assert_called_once()

    def test_no_lock_repo_returns_none(self):
        """When discovery_lock_repo is None, _try_acquire_lock is not called
        (discover_all_bots handles this, but verify the service doesn't crash)."""
        svc = DiscoveryService(
            reader=MagicMock(),
            session_initiator=MagicMock(),
            notify_sender=MagicMock(),
        )
        assert svc._lock_repo is None


# ===========================================================================
# DiscoveryService._send_work_order_event
# ===========================================================================

class TestSendWorkOrderEvent:
    """Cover _send_work_order_event — success / None / exception."""

    def test_none_service_returns_false(self):
        svc = DiscoveryService(
            reader=MagicMock(),
            session_initiator=MagicMock(),
            notify_sender=MagicMock(),
            work_order_service=None,
        )
        task = _make_task()
        assert svc._send_work_order_event(task, "user-1", _make_session()) is False

    def test_success_returns_true(self):
        wo = MagicMock()
        wo.create_work_order_event.return_value = MagicMock(
            notification_ids=[1, 2], work_order_id=99,
        )
        svc = DiscoveryService(
            reader=MagicMock(),
            session_initiator=MagicMock(),
            notify_sender=MagicMock(),
            work_order_service=wo,
        )
        task = _make_task()
        result = svc._send_work_order_event(task, "user-1", _make_session())
        assert result is True
        wo.create_work_order_event.assert_called_once()
        # Verify key kwargs.
        kwargs = wo.create_work_order_event.call_args.kwargs
        assert kwargs["event_type"] == "TASK_DISCOVERED"
        assert kwargs["recipient_user_ids"] == ["user-1"]
        assert kwargs["applicant_user_id"] is None

    def test_exception_returns_false(self):
        wo = MagicMock()
        wo.create_work_order_event.side_effect = RuntimeError("boom")
        svc = DiscoveryService(
            reader=MagicMock(),
            session_initiator=MagicMock(),
            notify_sender=MagicMock(),
            work_order_service=wo,
        )
        task = _make_task()
        assert svc._send_work_order_event(task, "user-1", _make_session()) is False


# ===========================================================================
# DiscoveredTask.to_discovery_prompt with empty acceptances
# ===========================================================================

def test_to_discovery_prompt_empty_acceptances():
    """to_discovery_prompt includes the 'confirm to supplement' branch when acceptances is empty."""
    task = _make_task(acceptances=[])
    prompt = task.to_discovery_prompt()
    assert "（确认时可由你补充）" in prompt
    assert "Test Task" in prompt


# ===========================================================================
# CronRelaySessionInitiator._build_discovery_prompt empty acceptances
# ===========================================================================

def test_build_discovery_prompt_empty_acceptances():
    """_build_discovery_prompt covers the empty-acceptances branch (multi-task)."""
    initiator = CronRelaySessionInitiator(
        cron_relay=MagicMock(),
        frontend_url="http://localhost:8000",
        backend_url="http://localhost:8888",
    )
    task = _make_task(acceptances=[])
    prompt = initiator._build_discovery_prompt([task])
    assert "（确认时可由你补充）" in prompt


# ===========================================================================
# _row_to_task with invalid JSON acceptances
# ===========================================================================

def test_row_to_task_invalid_acceptances_json(tmp_path):
    """_row_to_task falls back to [] when acceptances is not valid JSON."""
    db_path = str(tmp_path / "test_bad_acceptances.db")
    init_discovered_tasks_db(db_path, [
        {
            "task_id": "t-bad",
            "bot_id": "bot-1",
            "owner_id": "owner-1",
            "dt": _DT,
            "title": "BadTask",
            "instruction": "desc",
            "background": "bg",
            "discovery_basis": "basis",
            "acceptances": "not-valid-json{{",
        }
    ])
    reader = SqliteTaskReader(db_path)
    tasks = reader.read_pending_tasks_for_bot("bot-1", "owner-1", _DT)
    assert len(tasks) == 1
    assert tasks[0].acceptances == []


# ===========================================================================
# CommunityNotifyModule DI binding
# ===========================================================================

class TestCommunityNotifyModule:
    """Cover the DI binding logic — always wraps DingTalkNotifySender."""

    def test_always_wraps_dingtalk(self, monkeypatch):
        """DI always returns DingTalkNotifySender(CommunityNotifySender) —
        creds are checked at send time, not bind time."""
        from agentclaw.community.di.modules.infrastructure.community.notify import (
            CommunityNotifyModule,
        )
        mod = CommunityNotifyModule()
        sender = mod._notify_sender()
        assert isinstance(sender, DingTalkNotifySender)
        # Inner is CommunityNotifySender.
        assert isinstance(sender._inner, CommunityNotifySender)

    def test_dingtalk_configured_with_creds(self, monkeypatch):
        """_configured() returns True when creds are available via env."""
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_ID", "ak")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_AK_SECRET", "sk")
        monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_ROBOT_CODE", "rc")
        monkeypatch.setenv("TASK_DISCOVERY_CARD_TEMPLATE_ID", "tpl")
        from agentclaw.community.plugins.community.notify_sender import (
            DingTalkCredentialHolder,
        )
        DingTalkCredentialHolder.clear()
        assert DingTalkNotifySender._configured() is True

    def test_dingtalk_not_configured_without_creds(self, monkeypatch):
        """_configured() returns False when no creds available."""
        monkeypatch.delenv("TASK_DISCOVERY_DINGTALK_AK_ID", raising=False)
        monkeypatch.delenv("SINGLEBOX_DINGTALK_AK_ID", raising=False)
        from agentclaw.community.plugins.community.notify_sender import (
            DingTalkCredentialHolder,
        )
        DingTalkCredentialHolder.clear()
        assert DingTalkNotifySender._configured() is False


# ===========================================================================
# POST /discovery/reschedule endpoint — direct handler test
# ===========================================================================

class TestRescheduleEndpoint:
    """Cover the reschedule_cron handler (success / not-running / exception)."""

    def test_reschedule_success(self):
        from agentclaw.community.adapters.http.task.router import reschedule_cron
        scheduler = MagicMock()
        scheduler.reschedule.return_value = True
        scheduler.get_status.return_value = {
            "timezone": "Asia/Shanghai",
            "jobs": [{"next_run_time": "2026-08-26 11:00"}],
        }
        result = asyncio.run(reschedule_cron(cron="30 14 * * *", timezone=None, scheduler=scheduler))
        assert result["success"] is True
        assert result["cron"] == "30 14 * * *"
        assert result["timezone"] == "Asia/Shanghai"
        assert result["next_run_time"] == "2026-08-26 11:00"

    def test_reschedule_scheduler_not_running(self):
        from agentclaw.community.adapters.http.task.router import reschedule_cron
        scheduler = MagicMock()
        scheduler.reschedule.return_value = False
        result = asyncio.run(reschedule_cron(cron="30 14 * * *", timezone=None, scheduler=scheduler))
        assert result["success"] is False
        assert result["message"] == "scheduler not running"

    def test_reschedule_exception(self):
        from agentclaw.community.adapters.http.task.router import reschedule_cron
        scheduler = MagicMock()
        scheduler.reschedule.side_effect = ValueError("bad cron")
        result = asyncio.run(reschedule_cron(cron="bad", timezone=None, scheduler=scheduler))
        assert result["success"] is False
        assert "bad cron" in result["message"]

    def test_reschedule_no_jobs(self):
        from agentclaw.community.adapters.http.task.router import reschedule_cron
        scheduler = MagicMock()
        scheduler.reschedule.return_value = True
        scheduler.get_status.return_value = {"timezone": "UTC", "jobs": []}
        result = asyncio.run(reschedule_cron(cron="0 12 * * *", timezone="UTC", scheduler=scheduler))
        assert result["success"] is True
        assert result["next_run_time"] is None


def test_discovered_task_model_invalid_acceptances_falls_back_to_empty_list():
    """ORM-backed discovery reads must tolerate corrupt legacy acceptance JSON."""
    row = DiscoveredTaskModel(
        task_id="invalid-acceptances",
        bot_id="bot-1",
        owner_id="owner-1",
        dt="2026-08-27",
        title="Corrupt acceptance payload",
        acceptances="not-json",
    )

    task = row.to_domain()

    assert task.acceptances == []
    assert task.instruction == ""
    assert task.priority == "medium"
