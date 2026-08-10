"""Tests for env isolation — queries only return rows matching current env.

After the repo-to-database-plugin refactoring, all repo methods use
self-managed sessions.  Env is determined via ``get_current_env()``
inside each method; tests patch that function for isolation.
"""
from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from .conftest import FakeDB

from agentclaw.community.core.economy.governance.orm import WhitelistEntryOrm, GovernanceNotificationOrm
from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository
from agentclaw.community.core.repository.implementations.governance.whitelist import GovernanceWhitelistRepository


_NOTIFY_ENV = "agentclaw.community.core.repository.implementations.governance.notify_log.get_current_env"
_WHITELIST_ENV = "agentclaw.community.core.repository.implementations.governance.whitelist.get_current_env"


def _db(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return FakeDB(lambda: Session(bind=engine))


def _make_notification(session, *, notification_id, env="dev", governance_status="open", **overrides):
    row = GovernanceNotificationOrm(
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


# ── WhitelistRepository env isolation ──────────────────────────


class TestWhitelistEnvIsolation:

    def test_is_whitelisted_isolated(self, session, engine):
        session.add(WhitelistEntryOrm(
            bot_id="bot-dev", owner_id="user-1", whitelist_type="governance",
            source="manual", env="dev",
        ))
        session.add(WhitelistEntryOrm(
            bot_id="bot-pre", owner_id="user-1", whitelist_type="governance",
            source="manual", env="pre",
        ))
        session.commit()

        repo = GovernanceWhitelistRepository(db=_db(engine))
        with patch(_WHITELIST_ENV, return_value="dev"):
            assert repo.is_whitelisted("bot-dev", "user-1") is True
            assert repo.is_whitelisted("bot-pre", "user-1") is False

    def test_count_by_type_isolated(self, session, engine):
        session.add(WhitelistEntryOrm(
            bot_id="bot-dev", owner_id="user-1", whitelist_type="governance",
            source="manual", env="dev",
        ))
        session.add(WhitelistEntryOrm(
            bot_id="bot-pre", owner_id="user-1", whitelist_type="governance",
            source="manual", env="pre",
        ))
        session.commit()

        repo = GovernanceWhitelistRepository(db=_db(engine))
        with patch(_WHITELIST_ENV, return_value="dev"):
            assert repo.count_by_type() == 1
        with patch(_WHITELIST_ENV, return_value="pre"):
            assert repo.count_by_type() == 1