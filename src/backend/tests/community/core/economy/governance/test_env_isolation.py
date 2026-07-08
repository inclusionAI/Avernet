"""Tests for env isolation — queries only return rows matching current env.

After the repo-to-database-plugin refactoring, all repo methods use
self-managed sessions.  Env is determined via ``get_current_env()``
inside each method; tests patch that function for isolation.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from .conftest import FakeDB

from agentclaw.community.core.economy.governance.contracts.models import (
    BotWhitelist,
    GovernanceAudit,
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)


_NOTIFY_ENV = "agentclaw.community.core.economy.governance.repositories.notify_log_repo.get_current_env"
_TASK_ENV = "agentclaw.community.core.economy.governance.repositories.task_record_repo.get_current_env"
_WHITELIST_ENV = "agentclaw.community.core.economy.governance.repositories.whitelist_repo.get_current_env"
_AUDIT_ENV = "agentclaw.community.core.economy.governance.repositories.audit_repo.get_current_env"


def _db(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _make_notification(session, *, notification_id, env="dev", governance_status="open", **overrides):
    row = GovernanceNotifyLog(
        notification_id=notification_id,
        bot_id=overrides.pop("bot_id", f"bot-{notification_id}"),
        bot_name=overrides.pop("bot_name", "TestBot"),
        owner_id=overrides.pop("owner_id", "user-1"),
        worker_id=overrides.pop("worker_id", f"user-1:bot-{notification_id}"),
        dt_version="20260629",
        governance_decision="actionable",
        governance_cycle_id="cycle-1",
        governance_status=governance_status,
        notify_status="pending",
        latest_decision="actionable",
        consecutive_normal_days=0,
        remind_count=0,
        send_attempt_count=0,
        response=overrides.pop("response", None),
        env=env,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


# ── NotifyLogRepository env isolation ──────────────────────────


class TestNotifyLogEnvIsolation:

    def test_find_by_status_isolated(self, session, engine):
        _make_notification(session, notification_id="n-dev", env="dev", governance_status="open")
        _make_notification(session, notification_id="n-pre", env="pre", governance_status="open")

        repo = NotifyLogRepository(db=_db(engine))
        with patch(_NOTIFY_ENV, return_value="dev"):
            row = repo.find_by_status("bot-n-dev", "user-1", "open")
            assert row is not None
            assert row["env"] == "dev"
        with patch(_NOTIFY_ENV, return_value="pre"):
            row_pre = repo.find_by_status("bot-n-pre", "user-1", "open")
            assert row_pre is not None
            assert row_pre["env"] == "pre"

    def test_list_by_status_isolated(self, session, engine):
        _make_notification(session, notification_id="n-dev-1", env="dev", governance_status="open")
        _make_notification(session, notification_id="n-pre-1", env="pre", governance_status="open")

        repo = NotifyLogRepository(db=_db(engine))
        with patch(_NOTIFY_ENV, return_value="dev"):
            rows = repo.list_by_status("open")
            assert len(rows) == 1
            assert rows[0]["env"] == "dev"

    def test_count_pending_isolated(self, session, engine):
        _make_notification(session, notification_id="n-dev", env="dev", governance_status="open")
        _make_notification(session, notification_id="n-pre", env="pre", governance_status="open")

        repo = NotifyLogRepository(db=_db(engine))
        with patch(_NOTIFY_ENV, return_value="dev"):
            assert repo.count_pending() == 1
        with patch(_NOTIFY_ENV, return_value="pre"):
            assert repo.count_pending() == 1

    def test_count_open_muted_isolated(self, session, engine):
        _make_notification(session, notification_id="n-dev", env="dev", governance_status="open")
        _make_notification(session, notification_id="n-pre", env="pre", governance_status="muted", response="need_time")

        repo = NotifyLogRepository(db=_db(engine))
        with patch(_NOTIFY_ENV, return_value="dev"):
            assert repo.count_open_muted() == 1
        with patch(_NOTIFY_ENV, return_value="pre"):
            assert repo.count_open_muted() == 1

    def test_list_open_muted_isolated(self, session, engine):
        _make_notification(session, notification_id="n-dev", env="dev", governance_status="open")
        _make_notification(session, notification_id="n-pre", env="pre", governance_status="open")

        repo = NotifyLogRepository(db=_db(engine))
        with patch(_NOTIFY_ENV, return_value="pre"):
            rows = repo.list_open_muted()
            assert len(rows) == 1
            assert rows[0]["env"] == "pre"

    def test_get_last_scan_time_isolated(self, session, engine):
        now = datetime.now()
        session.add(GovernanceAudit(
            run_id="dev-run", action_taken="enqueued", env="dev", gmt_create=now,
        ))
        session.add(GovernanceAudit(
            run_id="pre-run", action_taken="enqueued", env="pre", gmt_create=now,
        ))
        session.commit()

        audit_repo = GovernanceAuditRepository(db=_db(engine))
        with patch(_AUDIT_ENV, return_value="dev"):
            result = audit_repo.get_last_scan_time()
            assert result is not None


# ── TaskRecordRepository env isolation ─────────────────────────


class TestTaskRecordEnvIsolation:

    def test_get_actionable_bots_isolated(self, session, engine):
        now = datetime.now()
        session.add(GovernanceTaskRecordDaily(
            worker_id="user-1:bot-dev", bot_id="bot-dev", dt_version="20260629",
            governance_decision="actionable", bot_name="DevBot",
            analysis_status="success", last_sync_at=now, env="dev",
        ))
        session.add(GovernanceTaskRecordDaily(
            worker_id="user-1:bot-pre", bot_id="bot-pre", dt_version="20260629",
            governance_decision="actionable", bot_name="PreBot",
            analysis_status="success", last_sync_at=now, env="pre",
        ))
        session.commit()

        repo = TaskRecordRepository(db=_db(engine))
        with patch(_TASK_ENV, return_value="dev"):
            rows = repo.get_actionable_bots("20260629")
            assert len(rows) == 1
            assert rows[0]["bot_id"] == "bot-dev"

    def test_get_max_last_sync_at_isolated(self, session, engine):
        now = datetime.now()
        session.add(GovernanceTaskRecordDaily(
            worker_id="user-1:bot-dev", bot_id="bot-dev", dt_version="20260629",
            governance_decision="actionable", bot_name="DevBot",
            analysis_status="success", last_sync_at=now, env="dev",
        ))
        session.commit()

        repo = TaskRecordRepository(db=_db(engine))
        with patch(_TASK_ENV, return_value="dev"):
            assert repo.get_max_last_sync_at() is not None
        with patch(_TASK_ENV, return_value="pre"):
            assert repo.get_max_last_sync_at() is None


# ── WhitelistRepository env isolation ──────────────────────────


class TestWhitelistEnvIsolation:

    def test_get_whitelist_set_isolated(self, session, engine):
        session.add(BotWhitelist(
            bot_id="bot-dev", owner_id="user-1", whitelist_type="governance",
            source="manual", env="dev",
        ))
        session.add(BotWhitelist(
            bot_id="bot-pre", owner_id="user-1", whitelist_type="governance",
            source="manual", env="pre",
        ))
        session.commit()

        repo = GovernanceWhitelistRepository(db=_db(engine))
        with patch(_WHITELIST_ENV, return_value="dev"):
            wl = repo.get_whitelist_set()
            assert ("bot-dev", "user-1") in wl
            assert ("bot-pre", "user-1") not in wl

    def test_count_by_type_isolated(self, session, engine):
        session.add(BotWhitelist(
            bot_id="bot-dev", owner_id="user-1", whitelist_type="governance",
            source="manual", env="dev",
        ))
        session.add(BotWhitelist(
            bot_id="bot-pre", owner_id="user-1", whitelist_type="governance",
            source="manual", env="pre",
        ))
        session.commit()

        repo = GovernanceWhitelistRepository(db=_db(engine))
        with patch(_WHITELIST_ENV, return_value="dev"):
            assert repo.count_by_type() == 1
        with patch(_WHITELIST_ENV, return_value="pre"):
            assert repo.count_by_type() == 1