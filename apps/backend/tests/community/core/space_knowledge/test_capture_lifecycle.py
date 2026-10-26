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


def test_library_writer_without_lifespan_cannot_commit_past_history_guard(database):
    from agentclaw.community.core.repository.implementations.spaces.space import (
        SpaceRepository,
    )
    from agentclaw.community.core.spaces.repository.models import SpaceModel

    with database.orm_session() as session:
        session.add(
            Snapshot(
                env="pre",
                space_id=7,
                revision=1,
                app="agentclaw",
                payload="{}",
                snapshot_hash="x" * 64,
                status="disabled",
                acknowledged=True,
            )
        )
    spaces = SpaceRepository(database, capture(database))
    with pytest.raises(RuntimeError, match="keep capture enabled"):
        with spaces.create_team_transaction(
            name="Must rollback", creator_id="owner", creator_user_name=None, env="pre"
        ):
            pytest.fail("guard must reject before caller receives identity")
    with database.orm_session() as session:
        assert session.query(SpaceModel).count() == 0


def test_library_producer_rejects_disabled_capture_before_snapshot_or_sink(database):
    from injector import Module
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.space_knowledge_capture_config import (
        SpaceKnowledgeCaptureConfig,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugin_api.space_membership import SpaceMembershipSink
    from agentclaw.community.core.space_knowledge.membership_sync import (
        SpaceMembershipSynchronizer,
    )
    from agentclaw.community.core.spaces.repository.models import (
        SpaceModel,
        SpaceMemberModel,
    )

    class Sink:
        calls = 0

        def is_enabled(self):
            return True

        def apply_snapshot(self, snapshot):
            self.calls += 1

    sink = Sink()

    class ProducerGraph(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=database)
            binder.bind(SpaceMembershipSink, to=sink)
            binder.bind(SpaceKnowledgeCaptureConfig, to=SpaceKnowledgeCaptureConfig())

    with database.orm_session() as session:
        session.add(
            SpaceModel(
                id=7,
                space_code="s7",
                space_type="TEAM",
                name="Team",
                env="pre",
                created_by="owner",
                updated_by="owner",
            )
        )
        session.add(
            SpaceMemberModel(
                space_id=7, user_id="owner", role="ADMIN", env="pre", created_by="owner"
            )
        )
    graph = build_injector(profile=DeployProfile.TEST, extra_modules=[ProducerGraph()])
    with pytest.raises(RuntimeError, match="capture"):
        graph.get(SpaceMembershipSynchronizer).ensure_synced(
            space_id=7, environment="pre"
        )
    with database.orm_session() as session:
        assert session.query(Snapshot).count() == 0
    assert sink.calls == 0


@pytest.mark.parametrize("operation", ["activate", "next_snapshot"])
def test_library_repository_cannot_bypass_capture_with_a_disabled_sink(
    database, operation
):
    from injector import Module
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.space_knowledge_capture_config import (
        SpaceKnowledgeCaptureConfig,
    )
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.core.repository.protocols.spaces import (
        SpaceKnowledgeSyncRepositoryProtocol,
    )

    class ExistingDatabase(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=database)
            binder.bind(SpaceKnowledgeCaptureConfig, to=SpaceKnowledgeCaptureConfig())

    graph = build_injector(
        profile=DeployProfile.TEST, extra_modules=[ExistingDatabase()]
    )
    repository = graph.get(SpaceKnowledgeSyncRepositoryProtocol)
    with pytest.raises(RuntimeError, match="producer requires capture"):
        getattr(repository, operation)(space_id=7, environment="pre", app="agentclaw")
    with database.orm_session() as session:
        assert session.query(Snapshot).count() == 0
