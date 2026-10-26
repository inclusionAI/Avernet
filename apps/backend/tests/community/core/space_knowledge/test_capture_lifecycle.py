"""Explicit recording and delivery are independent, validated lifecycle states."""

import asyncio

import pytest

from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import (
    KnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
    NoKnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.space_knowledge.capture_lifecycle import (
    SpaceKnowledgeCaptureLifecycle,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.plugins.unavailable.space_knowledge import (
    DisabledSpaceMembershipSink,
)
from tests.community.core.space_knowledge.test_membership_real_database import (
    database as database,
)


def capture(database, *, enabled=False, sink=None):
    return SpaceKnowledgeCaptureLifecycle(
        database,
        KnowledgeSpacePersistenceEffects()
        if enabled
        else NoKnowledgeSpacePersistenceEffects(),
        DisabledSpaceMembershipSink() if sink is None else sink,
        enabled=enabled,
        environment="pre",
    )


@pytest.mark.parametrize("owner_app", ["agentclaw", "other-app"])
def test_retained_history_refuses_empty_policy_even_with_delivery_disabled(
    database, owner_app
):
    with database.orm_session() as session:
        session.add(
            Snapshot(
                env="pre",
                space_id=7,
                revision=1,
                app=owner_app,
                payload="{}",
                snapshot_hash="x" * 64,
                status="disabled",
                acknowledged=True,
            )
        )
    with pytest.raises(RuntimeError, match="keep capture enabled"):
        asyncio.run(capture(database).startup())
    # Pausing delivery is safe only while the record policy remains enabled.
    asyncio.run(capture(database, enabled=True).startup())


def test_other_environment_history_does_not_enable_this_deployment(database):
    with database.orm_session() as session:
        session.add(
            Snapshot(
                env="prod",
                space_id=7,
                revision=1,
                app="agentclaw",
                payload="{}",
                snapshot_hash="x" * 64,
                status="active",
            )
        )
    asyncio.run(capture(database).startup())


def test_enabled_capture_refuses_missing_schema_without_fallback(database):
    Snapshot.__table__.drop(database._engine)
    with pytest.raises(RuntimeError, match="requires.*migration"):
        asyncio.run(capture(database, enabled=True).startup())
    asyncio.run(capture(database).startup())


def test_existing_incompatible_schema_is_not_treated_as_missing(database):
    with database._engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE ac_space_knowledge_sync")
        connection.exec_driver_sql("CREATE TABLE ac_space_knowledge_sync (env TEXT)")
    with pytest.raises(RuntimeError, match="schema is incompatible"):
        asyncio.run(capture(database).startup())


def test_sink_cannot_enable_delivery_without_recording(database):
    class EnabledSink:
        def is_enabled(self):
            return True

    with pytest.raises(RuntimeError, match="delivery requires capture"):
        asyncio.run(capture(database, sink=EnabledSink()).startup())
