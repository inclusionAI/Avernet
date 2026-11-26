import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_dormant.notify_log import commit_notify_log_idempotent
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
