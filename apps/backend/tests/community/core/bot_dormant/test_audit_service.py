"""Tests for best-effort OpenAPI dormant audit persistence."""

from contextlib import contextmanager
from unittest.mock import MagicMock

from agentclaw.community.core.bot_dormant.audit_service import DormantAuditService


class _DB:
    def __init__(self, session):
        self.session = session

    @contextmanager
    def orm_session(self):
        yield self.session


def test_record_openapi_recycle_writes_expected_audit_row():
    session = MagicMock()
    service = DormantAuditService(_DB(session))

    service.record_openapi_recycle(
        request_id="trace-1",
        bot_id="b1",
        owner_id="u1",
    )

    row = session.add.call_args.args[0]
    assert row.run_id == "trace-1"
    assert row.bot_id == "b1"
    assert row.owner_id == "u1"
    assert row.check_result == "manual"
    assert row.action_taken == "recycled"
    assert row.days_inactive is None
    assert row.source == "openapi"
    assert row.dry_run == 0
    session.commit.assert_called_once_with()


def test_record_openapi_recycle_does_not_raise_when_audit_fails():
    session = MagicMock()
    session.commit.side_effect = RuntimeError("db unavailable")
    service = DormantAuditService(_DB(session))

    service.record_openapi_recycle(
        request_id="trace-1",
        bot_id="b1",
        owner_id="u1",
    )
