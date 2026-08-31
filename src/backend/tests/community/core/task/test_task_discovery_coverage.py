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
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
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
    OrmTaskReader,
)
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
)
from agentclaw.community.plugins.community.notify_sender import (
    CommunityNotifySender,
    DingTalkCredentialHolder,
    DingTalkNotifySender,
    DingTalkYamlHolder,
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

    def test_resolve_prefers_api_holder(self):
        """_resolve returns from DingTalkCredentialHolder when set (line 169)."""
        DingTalkCredentialHolder.set("ak", "sk", "rc", "tpl")
        try:
            assert DingTalkNotifySender._resolve("ak_id", "NOPE_AK", "NOPE_AK2") == "ak"
        finally:
            DingTalkCredentialHolder.clear()

    @pytest.mark.parametrize(
        ("api_value", "yaml_value", "expected"),
        [("api-ak", "yaml-ak", "api"), ("", "yaml-ak", "yaml")],
    )
    def test_resolve_source_reports_api_and_yaml(self, api_value, yaml_value, expected):
        DingTalkCredentialHolder.clear()
        DingTalkYamlHolder.clear()
        try:
            if api_value:
                DingTalkCredentialHolder.set(api_value, "sk", "robot", "template")
            DingTalkYamlHolder.set({"ak_id": yaml_value})

            assert DingTalkNotifySender._resolve_source("ak_id") == expected
        finally:
            DingTalkCredentialHolder.clear()
            DingTalkYamlHolder.clear()

    @staticmethod
    def _install_fake_dingtalk_sdk(monkeypatch, ret_code: str):
        """Install the lazy-imported DingTalk SDK seam without real credentials."""
        class Config:
            pass

        class HttpHeader:
            pass

        class AccountContext:
            def __init__(self, account_id):
                self.account_id = account_id

        class SendRobotInteractiveCardRequest:
            pass

        class Body:
            def to_map(self):
                return {"retCode": ret_code}

        class Client:
            def __init__(self, config):
                self.config = config
                self.request = None
                self.headers = None

            def send_robot_interactive_card_with_options(self, request, headers, _options):
                self.request = request
                self.headers = headers
                return SimpleNamespace(body=Body())

        openapi = ModuleType("alibabacloud_tea_openapi")
        openapi.models = SimpleNamespace(Config=Config)
        util = ModuleType("alibabacloud_tea_util")
        util.models = SimpleNamespace(RuntimeOptions=lambda: object())
        ant = ModuleType("alipay_antdingopensdk_client")
        ant.models = SimpleNamespace(
            HttpHeader=HttpHeader,
            AccountContext=AccountContext,
            SendRobotInteractiveCardRequest=SendRobotInteractiveCardRequest,
        )
        client = ModuleType("alipay_antdingopensdk_client.client")
        client.Client = Client
        tea = ModuleType("Tea")

        monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi)
        monkeypatch.setitem(sys.modules, "alibabacloud_tea_util", util)
        monkeypatch.setitem(sys.modules, "alipay_antdingopensdk_client", ant)
        monkeypatch.setitem(sys.modules, "alipay_antdingopensdk_client.client", client)
        monkeypatch.setitem(sys.modules, "Tea", tea)

    @pytest.mark.parametrize("ret_code", ["0", "123"])
    def test_send_dingtalk_card_logs_success_and_business_failure(self, monkeypatch, ret_code):
        for name, value in {
            "TASK_DISCOVERY_DINGTALK_AK_ID": "ak",
            "TASK_DISCOVERY_DINGTALK_AK_SECRET": "sk",
            "TASK_DISCOVERY_DINGTALK_ROBOT_CODE": "robot",
            "TASK_DISCOVERY_CARD_TEMPLATE_ID": "template",
        }.items():
            monkeypatch.setenv(name, value)
        DingTalkCredentialHolder.clear()
        DingTalkYamlHolder.clear()
        self._install_fake_dingtalk_sdk(monkeypatch, ret_code)

        sender = DingTalkNotifySender(CommunityNotifySender())
        sender._send_dingtalk_card(
            NotifyMessage(
                title="Task",
                body="Body",
                recipient="owner-1",
                extra={"card_data": "{\"key\":\"value\"}"},
            )
        )


    def test_resolve_prefers_yaml_holder_over_env(self, monkeypatch):
        """_resolve returns from DingTalkYamlHolder when API holder is empty."""
        DingTalkCredentialHolder.clear()
        DingTalkYamlHolder.set({"ak_id": "yaml-ak", "empty_val": ""})
        try:
            assert DingTalkYamlHolder.get("ak_id") == "yaml-ak"
            assert DingTalkYamlHolder.get("empty_val") == ""
            assert DingTalkNotifySender._resolve(
                "ak_id", "TASK_DISCOVERY_DINGTALK_AK_ID", "SINGLEBOX_DINGTALK_AK_ID"
            ) == "yaml-ak"
        finally:
            DingTalkYamlHolder.clear()


# ===========================================================================
# DingTalkYamlHolder + DingTalkCredentialHolder
# ===========================================================================

class TestDingTalkHolders:
    """Cover holder set/get/clear paths."""

    def test_yaml_holder_set_filters_empty_values(self):
        """DingTalkYamlHolder.set() filters out empty-string values (line 66-68)."""
        DingTalkYamlHolder.set({"ak_id": "val", "empty": "", "none_val": None})
        assert DingTalkYamlHolder.get("ak_id") == "val"
        assert DingTalkYamlHolder.get("empty") == ""
        assert DingTalkYamlHolder.get("none_val") == ""
        DingTalkYamlHolder.clear()

    def test_yaml_holder_clear_resets_creds(self):
        """DingTalkYamlHolder.clear() resets to empty (line 79)."""
        DingTalkYamlHolder.set({"ak_id": "val"})
        assert DingTalkYamlHolder.get("ak_id") == "val"
        DingTalkYamlHolder.clear()
        assert DingTalkYamlHolder.get("ak_id") == ""

    def test_credential_holder_clear_resets_creds(self):
        """DingTalkCredentialHolder.clear() resets to empty."""
        DingTalkCredentialHolder.set("ak", "sk", "rc", "tpl")
        assert DingTalkCredentialHolder.get("ak_id") == "ak"
        DingTalkCredentialHolder.clear()
        assert DingTalkCredentialHolder.get("ak_id") == ""


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
# DiscoveredTaskModel.to_domain with invalid JSON acceptances
# ===========================================================================

def test_orm_reader_invalid_acceptances_json():
    """to_domain falls back to [] when acceptances column has non-JSON text."""
    from contextlib import contextmanager
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from agentclaw.community.core.base import Base
    from agentclaw.community.core.task.task_discovery.discovered_task_models import (  # noqa: F401
        DiscoveredTaskModel,
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)

    # Insert a row with invalid JSON in acceptances column directly
    db_session = factory()
    row = DiscoveredTaskModel(
        task_id="t-bad",
        bot_id="bot-1",
        owner_id="owner-1",
        dt=_DT,
        title="BadTask",
        instruction="desc",
        background="bg",
        discovery_basis="basis",
        acceptances="not-valid-json{{",
    )
    db_session.add(row)
    db_session.commit()
    db_session.close()

    class _TestDB:
        @contextmanager
        def orm_session(self):
            s = factory()
            try:
                yield s
                s.commit()
            finally:
                s.close()

    reader = OrmTaskReader(_TestDB())
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

    def test_notify_sender_loads_yaml_creds(self, monkeypatch):
        """DI reads task_discovery_dingtalk block from YAML config and sets
        DingTalkYamlHolder (notify.py lines 40-47)."""
        from agentclaw.community.di.modules.infrastructure.community.notify import (
            CommunityNotifyModule,
        )
        from agentclaw.community.di.modules import config_module

        DingTalkYamlHolder.clear()

        fake_cfg = {
            "ak_id": "yaml-ak",
            "ak_secret": "yaml-sk",
            "frontend_url": "http://fe.example.com",
        }
        with patch.object(config_module, "_block", return_value=fake_cfg):
            mod = CommunityNotifyModule()
            sender = mod._notify_sender()

        assert isinstance(sender, DingTalkNotifySender)
        assert DingTalkYamlHolder.get("ak_id") == "yaml-ak"
        assert DingTalkYamlHolder.get("ak_secret") == "yaml-sk"

        DingTalkYamlHolder.clear()

    def test_notify_sender_skips_yaml_when_block_empty(self, monkeypatch):
        """DI skips DingTalkYamlHolder.set when _block returns empty (line 39
        if-branch not taken)."""
        from agentclaw.community.di.modules.infrastructure.community.notify import (
            CommunityNotifyModule,
        )
        from agentclaw.community.di.modules import config_module

        DingTalkYamlHolder.clear()
        with patch.object(config_module, "_block", return_value={}):
            mod = CommunityNotifyModule()
            sender = mod._notify_sender()

        assert isinstance(sender, DingTalkNotifySender)
        assert DingTalkYamlHolder.get("ak_id") == ""


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
