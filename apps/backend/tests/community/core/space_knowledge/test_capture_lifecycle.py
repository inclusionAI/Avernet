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
@pytest.mark.parametrize("status", ["active", "disabled"])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_retained_history_does_not_block_disabled_capture_startup(
    database, owner_app, status, acknowledged
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
                status=status,
                acknowledged=acknowledged,
            )
        )
    asyncio.run(capture(database).startup())
    # Startup neither enables capture nor rewrites another deployment's history.
    with database.orm_session() as session:
        row = session.query(Snapshot).one()
        assert (row.app, row.status, row.acknowledged) == (
            owner_app,
            status,
            acknowledged,
        )
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


@pytest.mark.parametrize("owner_app", ["agentclaw", "other-app"])
def test_library_writer_without_lifespan_can_commit_with_retained_history(
    database, owner_app
):
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
                app=owner_app,
                payload="{}",
                snapshot_hash="x" * 64,
                status="disabled",
                acknowledged=True,
            )
        )
    spaces = SpaceRepository(database, capture(database))
    with spaces.create_team_transaction(
        name="Available without capture",
        creator_id="owner",
        creator_user_name=None,
        env="pre",
    ) as space:
        space_id = space.id
    assert spaces.get_space(space_id=space_id, env="pre").name == (
        "Available without capture"
    )
    with database.orm_session() as session:
        assert session.query(SpaceModel).count() == 1
        row = session.query(Snapshot).one()
        assert (row.space_id, row.revision, row.app) == (7, 1, owner_app)


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
@pytest.mark.parametrize("retained_history", [False, True])
def test_library_repository_cannot_bypass_capture_with_a_disabled_sink(
    database, operation, retained_history
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

    if retained_history:
        with database.orm_session() as session:
            session.add(
                Snapshot(
                    env="pre",
                    space_id=7,
                    revision=1,
                    app="other-app",
                    payload="{}",
                    snapshot_hash="x" * 64,
                    status="disabled",
                    acknowledged=True,
                )
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
        assert session.query(Snapshot).count() == int(retained_history)
