"""The internal effect implementations obey the caller-owned transaction seam."""

import pytest

from agentclaw.community.core.repository.protocols.space_persistence_effects import (
    SpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
    NoKnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import (
    KnowledgeSpacePersistenceEffects,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    SpaceKnowledgeSyncRepository,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.plugins.community.database import CommunityDatabase
from tests.community.core.space_knowledge.capture_fixtures import enabled_capture


def test_incomplete_contract_cannot_silently_inherit_noop_methods():
    class Incomplete(SpacePersistenceEffects):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()
    assert issubclass(NoKnowledgeSpacePersistenceEffects, SpacePersistenceEffects)
    assert issubclass(KnowledgeSpacePersistenceEffects, SpacePersistenceEffects)


def test_default_capture_and_identity_effects_need_no_database_at_all():
    effects = NoKnowledgeSpacePersistenceEffects()
    # Neither sentinel is an ORM session/model: accidental DB/identity use fails.
    session, space = object(), object()
    assert effects.record_membership(session, space) is None
    assert effects.record_membership(session, space, disabled=True) is None
    assert effects.reserve_identity(session, space) is None


def test_enabled_effect_uses_source_transaction_and_rolls_back_with_it(tmp_path):
    db = CommunityDatabase(f"sqlite:///{tmp_path / 'effects.db'}", create_schema=False)
    for model in (SpaceModel, SpaceMemberModel, Snapshot):
        model.__table__.create(db._engine)
    try:
        with db.transactional_orm_session() as session:
            session.add(
                SpaceModel(
                    id=1,
                    space_code="s1",
                    space_type="TEAM",
                    name="Before",
                    env="pre",
                    created_by="owner",
                    updated_by="owner",
                )
            )
        repo = SpaceKnowledgeSyncRepository(db, enabled_capture(db))
        repo.activate(space_id=1, environment="pre", app="agentclaw")
        effects = KnowledgeSpacePersistenceEffects()
        with pytest.raises(RuntimeError, match="later failure"):
            with db.transactional_orm_session() as session:
                space = effects.lock_space(session, space_id=1, environment="pre")
                space.name = "Never committed"
                effects.record_membership(session, space)
                assert session.query(Snapshot).count() == 2
                raise RuntimeError("later failure")
        with db.orm_session() as session:
            assert session.get(SpaceModel, 1).name == "Before"
            assert session.query(Snapshot).count() == 1
    finally:
        db._engine.dispose()
