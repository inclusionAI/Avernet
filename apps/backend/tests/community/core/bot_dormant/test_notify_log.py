import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_dormant.notify_log import (
    commit_notify_log_idempotent,
    flush_notify_log_idempotent,
)
from agentclaw.community.core.bot_dormant.sqlite_models import DormantNotifyLog


def test_commit_notify_log_reraises_non_unique_integrity_error():
    class _Session:
        def add(self, _row):
            return None

        def commit(self):
            raise IntegrityError("insert", {}, Exception("foreign key failed"))

        def rollback(self):
            return None

    row = DormantNotifyLog(
        bot_id="bot1",
        owner_id="owner1",
        entity_id="owner1",
        dt="20260702",
        notify_type="warn",
        notify_source="internal_scan",
        notify_target="owner1",
        content="hello",
    )

    with pytest.raises(IntegrityError):
        commit_notify_log_idempotent(_Session(), row)



def test_flush_notify_log_recovers_daily_unique_race_without_outer_transaction_rollback():
    existing = object()

    class _Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Query:
        def __init__(self):
            self.calls = 0

        def filter_by(self, **_kwargs):
            return self

        def first(self):
            self.calls += 1
            return None if self.calls == 1 else existing

    class _Session:
        def __init__(self):
            self.query_result = _Query()
            self.added = []
            self.flushes = 0

        def query(self, _model):
            return self.query_result

        def begin_nested(self):
            return _Savepoint()

        def add(self, row):
            self.added.append(row)

        def flush(self):
            self.flushes += 1
            raise IntegrityError(
                "insert", {},
                Exception("UNIQUE constraint failed: ac_bot_dormant_notify_log.bot_id"),
            )

    row = DormantNotifyLog(
        bot_id="bot1", owner_id="owner1", entity_id="owner1",
        dt="20260702", notify_type="recycle",
        notify_source="internal_scan", notify_target="owner1", content="hello",
    )
    session = _Session()

    assert flush_notify_log_idempotent(session, row) is False
    assert session.added == [row]
    assert session.flushes == 1
    assert session.query_result.calls == 2
