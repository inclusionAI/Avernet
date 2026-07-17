"""Unit tests for NotifyLogRepository — covers the branch/edge paths not
exercised by the service-level tests.

Targets the previously-uncovered methods:
  - get_by_notification_id_and_owner  (owner-scoped single lookup)
  - list_by_owner_and_statuses        (paged multi-status list)
  - list_distinct_bot_owner           (distinct (bot_id, owner_id) pairs)

Note: ``add_audit`` has been moved to ``GovernanceAuditRepository``
(see ``test_audit_repo.py``).

All repo methods use self-managed ``orm_session()``; env is determined
via ``get_current_env()`` inside each method. Tests that need env
isolation patch that function.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.repositories.orm import (
    GovernanceNotificationOrm,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)

from .conftest import FakeDB


_ENV_PATCH = "agentclaw.community.core.economy.governance.repositories.notify_log_repo.get_current_env"


def _make_notification(
    session,
    *,
    notification_id,
    env="dev",
    governance_status="open",
    **overrides,
):
    """Create and persist a minimal notify_log row (mirrors service-test factory)."""
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
        notify_status=overrides.pop("notify_status", "pending"),
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


def _build_repo(engine):
    """Build repo backed by in-memory SQLite with FakeDB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    return NotifyLogRepository(db=db), db


@pytest.fixture()
def repo(engine, tables):
    """Repository under test — built with real FakeDB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    return NotifyLogRepository(db=db)


# ----------------------------------------------------------------------
# get_by_notification_id_and_owner
# ----------------------------------------------------------------------

class TestGetByNotificationIdAndOwner:
    def test_returns_row_when_owner_matches(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-1", owner_id="user-1", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            row = repo.get_by_notification_id_and_owner("n-1", "user-1")
        assert row is not None
        assert row.notification_id == "n-1"
        assert row.owner_id == "user-1"

    def test_returns_none_when_owner_mismatch(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-1", owner_id="user-1", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            row = repo.get_by_notification_id_and_owner("n-1", "other-user")
        assert row is None

    def test_explicit_env_arg_bypasses_default(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-pre", owner_id="user-1", env="pre",
        )
        # get_current_env would be "dev", but patch to "pre" → finds the row.
        with patch(_ENV_PATCH, return_value="pre"):
            row = repo.get_by_notification_id_and_owner("n-pre", "user-1")
        assert row is not None
        # domain model 不暴露 env (sealed), 通过 notification_id 匹配确认找到正确行
        assert row.notification_id == "n-pre"


# ----------------------------------------------------------------------
# list_by_owner_and_statuses
# ----------------------------------------------------------------------

class TestListByOwnerAndStatuses:
    def test_filters_by_owner_and_statuses(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-open", owner_id="user-1",
            governance_status="open", env="dev",
        )
        _make_notification(
            session, notification_id="n-closed", owner_id="user-1",
            governance_status="closed", env="dev",
        )
        _make_notification(
            session, notification_id="n-muted", owner_id="user-1",
            governance_status="muted", env="dev",
        )
        # different owner — excluded
        _make_notification(
            session, notification_id="n-other", owner_id="user-2",
            governance_status="open", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            rows = repo.list_by_owner_and_statuses(
                "user-1", ["open", "muted"],
            )
        ids = {r.notification_id for r in rows}
        assert ids == {"n-open", "n-muted"}

    def test_pagination_offset_and_limit(self, session, engine, repo):
        for i in range(5):
            _make_notification(
                session, notification_id=f"n-{i}", owner_id="user-1",
                governance_status="open", env="dev",
            )
        with patch(_ENV_PATCH, return_value="dev"):
            page = repo.list_by_owner_and_statuses(
                "user-1", ["open"], offset=1, limit=2,
            )
        assert len(page) == 2

    def test_empty_when_no_match(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-open", owner_id="user-1",
            governance_status="open", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            rows = repo.list_by_owner_and_statuses(
                "user-1", ["expired"],
            )
        assert rows == []


# ----------------------------------------------------------------------
# list_distinct_bot_owner
# ----------------------------------------------------------------------

class TestListDistinctBotOwner:
    def test_returns_distinct_pairs(self, session, engine, repo):
        # two rows for the same (bot, owner) -> collapsed to one pair
        _make_notification(
            session, notification_id="n-a1", bot_id="bot-a",
            owner_id="user-1", env="dev",
        )
        _make_notification(
            session, notification_id="n-a2", bot_id="bot-a",
            owner_id="user-1", env="dev",
        )
        _make_notification(
            session, notification_id="n-b1", bot_id="bot-b",
            owner_id="user-2", env="dev",
        )
        # bot not requested -> excluded
        _make_notification(
            session, notification_id="n-c1", bot_id="bot-c",
            owner_id="user-3", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            pairs = repo.list_distinct_bot_owner(["bot-a", "bot-b"])
        assert set(pairs) == {("bot-a", "user-1"), ("bot-b", "user-2")}

    def test_empty_bot_ids_returns_empty(self, session, engine, repo):
        _make_notification(
            session, notification_id="n-a1", bot_id="bot-a",
            owner_id="user-1", env="dev",
        )
        with patch(_ENV_PATCH, return_value="dev"):
            pairs = repo.list_distinct_bot_owner([])
        assert pairs == []


# ----------------------------------------------------------------------
# add_audit — moved to GovernanceAuditRepository (see test_audit_repo.py)
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# count_by_ticket_id / delete_by_ticket_id — ticket-cascade data layer
# ----------------------------------------------------------------------

class TestByTicketId:
    """count_by_ticket_id / delete_by_ticket_id — ticket-cascade 支撑。

    env-scoped 按 ticket_id 精确计数/硬删;不同 env 同 ticket_id 不交叉;
    空结果 count=0 / delete=0。
    """

    def test_count_returns_rows_for_matching_ticket(self, session, engine, repo):
        _make_notification(session, notification_id="n-a", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-b", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-c", ticket_id="tkt-2", env="dev")
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.count_by_ticket_id("tkt-1") == 2
            assert repo.count_by_ticket_id("tkt-2") == 1

    def test_count_zero_when_no_match(self, session, engine, repo):
        _make_notification(session, notification_id="n-a", ticket_id="tkt-1", env="dev")
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.count_by_ticket_id("no-such-ticket") == 0

    def test_count_is_env_scoped(self, session, engine, repo):
        _make_notification(session, notification_id="n-dev", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-pre", ticket_id="tkt-1", env="pre")
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.count_by_ticket_id("tkt-1") == 1
        with patch(_ENV_PATCH, return_value="pre"):
            assert repo.count_by_ticket_id("tkt-1") == 1

    def test_delete_removes_all_rows_for_ticket_and_returns_count(self, session, engine, repo):
        _make_notification(session, notification_id="n-a", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-b", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-c", ticket_id="tkt-2", env="dev")
        with patch(_ENV_PATCH, return_value="dev"):
            deleted = repo.delete_by_ticket_id("tkt-1")
        assert deleted == 2
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.count_by_ticket_id("tkt-1") == 0
            assert repo.count_by_ticket_id("tkt-2") == 1

    def test_delete_zero_when_no_match(self, session, engine, repo):
        _make_notification(session, notification_id="n-a", ticket_id="tkt-1", env="dev")
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.delete_by_ticket_id("no-such-ticket") == 0

    def test_delete_is_env_scoped(self, session, engine, repo):
        _make_notification(session, notification_id="n-dev", ticket_id="tkt-1", env="dev")
        _make_notification(session, notification_id="n-pre", ticket_id="tkt-1", env="pre")
        with patch(_ENV_PATCH, return_value="dev"):
            assert repo.delete_by_ticket_id("tkt-1") == 1
        # pre env 的行仍在
        with patch(_ENV_PATCH, return_value="pre"):
            assert repo.count_by_ticket_id("tkt-1") == 1