"""Real fixed public candidate ORM; no copied schema/model or production gitlink change."""

import pytest
from agentclaw.community.core.knowledge_management.repository.source import (
    ExistingKnowledgeEnablementReader,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from sqlalchemy import UniqueConstraint

from .conftest import grant


@pytest.fixture
def candidate(world):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel,
    )

    binding = BotKnowledgeBindingModel
    binding.__table__.create(world.engine)
    world.service.enablement = ExistingKnowledgeEnablementReader(world.db, "pre")
    return binding


def test_real_candidate_fields_and_composite_unique_key(candidate):
    assert candidate.__tablename__ == "ac_bot_knowledge_binding"
    assert {"avernet_tenant", "env", "bot_pk", "space_id", "kb_id", "enabled"} <= set(
        candidate.__table__.columns.keys()
    )
    keys = {
        tuple(column.name for column in constraint.columns)
        for constraint in candidate.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("avernet_tenant", "env", "bot_pk", "kb_id") in keys


def test_real_candidate_bot_level_enablement_is_tenant_env_space_kb_scoped(
    world, candidate
):
    binding = candidate
    with world.db.orm_session() as session:
        session.add_all(
            [
                binding(
                    env="pre",
                    space_id=1,
                    kb_id="kb",
                    bot_pk=1,
                    enabled=True,
                    updated_by="owner",
                ),
                binding(
                    env="prod",
                    space_id=1,
                    kb_id="kb",
                    bot_pk=2,
                    enabled=True,
                    updated_by="owner",
                ),
                binding(
                    env="pre",
                    space_id=1,
                    kb_id="else",
                    bot_pk=2,
                    enabled=True,
                    updated_by="owner",
                ),
                binding(
                    env="pre",
                    space_id=2,
                    kb_id="kb",
                    bot_pk=2,
                    enabled=True,
                    updated_by="owner",
                ),
            ]
        )
    with avernet_tenant_scope("other-tenant"), world.db.orm_session() as session:
        session.add_all(
            [
                binding(
                    env="pre",
                    space_id=1,
                    kb_id="kb",
                    bot_pk=1,
                    enabled=False,
                    updated_by="other",
                ),
                binding(
                    env="pre",
                    space_id=1,
                    kb_id="kb",
                    bot_pk=2,
                    enabled=True,
                    updated_by="other",
                ),
            ]
        )
    world.catalog.grants["items"] = [grant(), grant(owner_id="other-owner")]
    world.sql.clear()
    result = world.service.authorized_bots(space_id=1, kb_id="kb", actor_id="actor")
    assert [item["enabled"] for item in result["items"]] == [True, False]
    queries = [sql for sql in world.sql if "ac_bot_knowledge_binding" in sql]
    assert len(queries) == 1
    assert "avernet_tenant" in queries[0]
    # This is stored Bot state, not actor/Caller-specific state. Updating only
    # the Bot's value is immediately reflected without changing a grant or ACL.
    with world.db.orm_session() as session:
        session.query(binding).filter_by(
            env="pre", space_id=1, kb_id="kb", bot_pk=1
        ).update({"enabled": False})
    assert (
        world.service.authorized_bots(space_id=1, kb_id="kb", actor_id="actor")[
            "items"
        ][0]["enabled"]
        is False
    )


def test_real_candidate_successfully_absent_binding_is_false(world, candidate):
    assert (
        world.service.authorized_bots(space_id=1, kb_id="kb", actor_id="actor")[
            "items"
        ][0]["enabled"]
        is False
    )


def test_real_candidate_missing_table_is_503_not_false(world, candidate):
    candidate.__table__.drop(world.engine)
    with pytest.raises(KnowledgeManagementError) as error:
        world.service.authorized_bots(space_id=1, kb_id="kb", actor_id="actor")
    assert not hasattr(error.value, "status")
    assert error.value.code == "KNOWLEDGE_ENABLEMENT_UNAVAILABLE"


@pytest.mark.parametrize("count", [1, 100])
def test_real_candidate_bulk_read_has_constant_query_count(world, candidate, count):
    from agentclaw.community.plugin_api.models import BotModel

    with world.db.orm_session() as session:
        for i in range(count):
            session.add(
                BotModel(
                    id=100 + i,
                    bot_id=f"bulk-{i}",
                    owner_id="owner",
                    entity_id="owner",
                    entity_type="user",
                    creator_id="owner",
                    env="pre",
                    space_id=1,
                    bot_name=f"Bulk {i}",
                )
            )
            session.add(
                candidate(
                    env="pre",
                    space_id=1,
                    kb_id="kb",
                    bot_pk=100 + i,
                    enabled=True,
                    updated_by="owner",
                )
            )
    world.catalog.grants["items"] = [grant(f"bulk-{i}") for i in range(count)]
    world.sql.clear()
    result = world.service.authorized_bots(
        space_id=1, kb_id="kb", actor_id="actor", limit=count
    )
    assert len(result["items"]) == count
    assert all(item["enabled"] is True for item in result["items"])
    assert sum("ac_bots" in sql for sql in world.sql if sql.startswith("SELECT")) == 1
    assert (
        sum(
            "ac_bot_knowledge_binding" in sql
            for sql in world.sql
            if sql.startswith("SELECT")
        )
        == 1
    )
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in world.sql)
