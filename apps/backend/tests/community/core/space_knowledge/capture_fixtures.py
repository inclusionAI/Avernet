"""Operator-managed real database and default, full application composition."""

from unittest.mock import AsyncMock

import pytest
from injector import Module
from sqlalchemy import inspect

from agentclaw.community.core.base import Base
from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.core.schema import import_all_models
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.community.database import CommunityDatabase


@pytest.fixture
def missing_knowledge_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "pre")
    monkeypatch.setitem(
        vars(sofa_config),
        "user_config",
        {
            "admin": {"super_admin": ["owner"]},
        },
    )
    import_all_models()
    db = CommunityDatabase(f"sqlite:///{tmp_path / 'legacy.db'}", create_schema=False)
    tables = [
        t for t in Base.metadata.sorted_tables if t.name != "ac_space_knowledge_sync"
    ]
    Base.metadata.create_all(db._engine, tables=tables)
    with db.orm_session() as session:
        session.add(
            SpaceModel(
                id=1,
                space_code="legacy",
                space_type="TEAM",
                name="Existing",
                created_by="owner",
                updated_by="owner",
                env="pre",
            )
        )
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=1, user_id=user, role=role, created_by="owner", env="pre"
                )
                for user, role in [("owner", "ADMIN"), ("alice", "MEMBER")]
            ]
        )
    auth = AsyncMock(spec=AuthPlugin)
    auth.resolve_user_from_request.return_value = AuthenticatedIdentity(
        id="owner",
        staffId="owner",
        operatorName="Owner",
    )

    class ExistingDatabase(Module):
        def configure(self, binder):
            binder.bind(DatabasePlugin, to=db)
            binder.bind(AuthPlugin, to=auth)

    graph = build_injector(
        profile=DeployProfile.TEST, extra_modules=[ExistingDatabase()]
    )
    assert not inspect(db._engine).has_table("ac_space_knowledge_sync")
    yield graph, db
    assert not inspect(db._engine).has_table("ac_space_knowledge_sync")
    db._engine.dispose()


def enabled_capture(database):
    """Explicit recording policy for standalone producer contract/race tests."""
    from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import (
        KnowledgeSpacePersistenceEffects,
    )
    from agentclaw.community.core.space_knowledge.capture_lifecycle import (
        SpaceKnowledgeCaptureLifecycle,
    )
    from agentclaw.community.plugins.unavailable.space_knowledge import (
        DisabledSpaceMembershipSink,
    )

    return SpaceKnowledgeCaptureLifecycle(
        database,
        KnowledgeSpacePersistenceEffects(),
        DisabledSpaceMembershipSink(),
        enabled=True,
        environment="pre",
    )
