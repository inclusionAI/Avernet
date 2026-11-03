import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)


def test_real_sqlite_writer_contention_does_not_partially_write(knowledge):
    db, _, service, scope = knowledge
    engine = db.factory.kw["bind"]
    with engine.connect() as blocker:
        blocker.exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError) as error:
            service.set_enabled(
                scope=scope,
                actor_id="owner",
                kb_id="kb",
                enabled=True,
                expected_revision=0,
            )
        assert error.value.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
        blocker.rollback()
    with db.orm_session() as session:
        assert session.query(BotKnowledgeBindingModel).count() == 0
    assert (
        service.set_enabled(
            scope=scope,
            actor_id="owner",
            kb_id="kb",
            enabled=True,
            expected_revision=0,
        )["revision"]
        == 1
    )
