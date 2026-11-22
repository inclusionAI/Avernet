"""Committed capability changes and recovery records are one transaction."""

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredStateRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
    CapabilityProjectionRepository,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.plugin_api.models import BotModel

from .capability_desired_state_support import _Database


def test_claim_rolls_back_when_pending_projection_cannot_be_persisted():
    db = _Database()
    desired = CapabilityDesiredStateRepository(db)
    projection = CapabilityProjectionRepository(db)
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_projection
            BEFORE INSERT ON ac_bot_capability_projection
            BEGIN
              SELECT RAISE(ABORT, 'cannot write projection');
            END
            """
        )

    with pytest.raises(IntegrityError, match="cannot write projection"):
        desired.commit_projection_mutation(
            bot_id="bot",
            entity_id="owner",
            mutation=lambda: desired.claim_apply_mcp(
                bot_id="bot",
                owner_id="owner",
                server_code="new-server",
                config={},
                platform_default_codes=frozenset(),
            ),
            scope_from_result=lambda result: ProjectionScope(
                mcp=True, claimed_mcp=result.mcp_codes,
            ),
            retired_mappings=(),
            create=True,
        )

    assert desired.list_installed_mcps(bot_id="bot", owner_id="owner") == set()
    assert projection.read(bot_id="bot", entity_id="owner") is None



def test_known_old_receipt_releases_the_dispatch_fence_without_confirming_newer_work():
    repository = CapabilityProjectionRepository(_Database())
    locator = {"bot_id": "bot", "entity_id": "owner"}
    first = repository.require(**locator, scope=ProjectionScope(skills=True))
    assert repository.begin(first)
    assert not repository.begin(first)

    newer = repository.require(
        **locator, scope=ProjectionScope(mcp=True, released_mcp=frozenset({"withdrawn"})),
    )
    assert newer.write_blocked
    assert repository.due(env="dev", limit=10) == ()

    assert not repository.complete(first, RuntimeProjectionResult.converged())
    pending = repository.read(**locator)
    assert pending is not None and pending.version == newer.version
    assert pending.result.status is RuntimeProjectionStatus.PENDING
    assert pending.scope.released_mcp == frozenset({"withdrawn"})
    assert pending.needs_recovery
    assert len(repository.due(env="dev", limit=10)) == 1


def test_a_new_binding_reconciles_without_accepting_an_old_binding_receipt():
    db = _Database()
    repository = CapabilityProjectionRepository(db)
    locator = {"bot_id": "bot", "entity_id": "owner"}
    with db.transactional_orm_session() as session:
        bot = session.query(BotModel).filter_by(**locator).one()
        bot.binding_id = 17
        bot.device_id = "original"
    first = repository.require(**locator, scope=ProjectionScope(skills=True))
    assert repository.begin(first)
    assert repository.complete(
        first, RuntimeProjectionResult.pending(code="NO_RECEIPT", reason="unknown"),
        write_blocked=True,
    )

    with db.transactional_orm_session() as session:
        bot = session.query(BotModel).filter_by(**locator).one()
        bot.binding_id = 42
        bot.device_id = "replacement"

    observed = repository.read(**locator)
    assert observed is not None and not observed.is_current_binding
    assert observed.result.status is RuntimeProjectionStatus.PENDING
    assert len(repository.due(env="dev", limit=10)) == 1
    assert not repository.complete(first, RuntimeProjectionResult.converged())

    current = repository.prepare(**locator)
    assert current is not None and current.binding_id == 42
    assert current.is_current_binding and current.version > first.version
    assert not current.write_blocked and current.scope == ProjectionScope.everything()
    assert repository.complete(current, RuntimeProjectionResult.converged())
    assert repository.due(env="dev", limit=10) == ()


def test_retry_retains_only_the_unconfirmed_domain_and_keeps_its_retirement_evidence():
    repository = CapabilityProjectionRepository(_Database())
    locator = {"bot_id": "bot", "entity_id": "owner"}
    retired = PoolSkillMapping(corpus="local", relative_path="retired", link_name="retired")
    work = repository.require(
        **locator, scope=ProjectionScope(skills=True, mcp=True), retired_mappings=(retired,),
    )
    assert repository.complete(work, RuntimeProjectionResult(
        status=RuntimeProjectionStatus.PENDING,
        components={
            "skills": RuntimeProjectionStatus.PENDING,
            "mcp": RuntimeProjectionStatus.CONVERGED,
        },
    ))
    pending = repository.prepare(**locator)
    assert pending is not None
    assert pending.scope == ProjectionScope(skills=True)
    assert pending.retired_mappings == (retired,)

    assert repository.complete(pending, RuntimeProjectionResult.converged(
        components={"skills": RuntimeProjectionStatus.CONVERGED},
    ))
    completed = repository.read(**locator)
    assert completed is not None and completed.retired_mappings == ()
    assert completed.result.components == {
        "skills": RuntimeProjectionStatus.CONVERGED,
        "mcp": RuntimeProjectionStatus.CONVERGED,
    }
