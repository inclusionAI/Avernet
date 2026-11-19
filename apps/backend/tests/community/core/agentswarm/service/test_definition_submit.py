"""Submit by agent_refs: resolution, refusals, and provenance.

Resolution runs after the idempotency join and before admission, so a refused
roster consumes no tenant capacity and a duplicate submit never re-reads
definitions. Provenance is written beside the catalog snapshot, never inside it.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, LlmConfig,
)
from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.agent_definition import (
    AgentDefinitionRepository,
)
from agentclaw.community.core.agentswarm.repository.agent_result import (
    AgentResultRepository,
)
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.repository.run_agent_ref import (
    RunAgentRefRepository,
)
from agentclaw.community.core.agentswarm.repository.run_dispatch import (
    RunDispatchRepository,
)
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentDefinitionRef, AgentReferenceUnresolved, SubmitRunRequest,
)


class _TestDB:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    session = orm_session


class _Validator:
    def validate_document(self, document: str) -> None:
        return None


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        om.AcAgentSwarmAgentDefinitionModel.__table__,
        om.AcAgentSwarmRunAgentRefModel.__table__,
        om.AcAgentSwarmRunModel.__table__,
        om.AcAgentSwarmRunDispatchModel.__table__,
        om.AcAgentSwarmAgentModel.__table__,
        om.AcAgentSwarmMessageModel.__table__,
        om.AcAgentSwarmAgentResultModel.__table__,
        om.AcAgentSwarmRoundModel.__table__,
        om.AcAgentSwarmGuardEventModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


TENANT = "teamclaw"
ALICE = "49912"
BOB = "50087"
IDENTITY = "20261117_dfpm4nls"


def _config() -> AgentSwarmConfig:
    return AgentSwarmConfig(llm=LlmConfig(api_key="k", base_url="http://x"))


def _service(db) -> AgentSwarmService:
    return AgentSwarmService(
        run_repo=RunRepository(db),
        dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db),
        attempt_repo=AgentAttemptRepository(db),
        config=_config(),
        definition_manifest_validator=_Validator(),
        definition_repo=AgentDefinitionRepository(db),
        run_agent_ref_repo=RunAgentRefRepository(db),
    )


def _req(refs, *, owner=ALICE, key=None) -> SubmitRunRequest:
    return SubmitRunRequest(
        tenant=TENANT, goal="g", agents=(), owner_id=owner,
        idempotency_key=key, identity_bot_id=IDENTITY,
        agent_refs=tuple(refs),
    )


def _mk_definition(svc, *, name="reviewer", owner=ALICE, role="checks"):
    return svc.create_definition(
        tenant=TENANT, owner_id=owner, name=name, role=role,
        kind=dm.AgentKind.BOT, engine="openclaw",
    )


def _submit(svc, req):
    # asyncio.run, not get_event_loop: a sibling module in this package closes
    # the thread's loop, so borrowing "the current" one is order-dependent.
    return asyncio.run(svc.submit_async(req))


# ── resolution ──────────────────────────────────────────────────────────────

def test_reference_resolves_to_the_definition_spec(db):
    svc = _service(db)
    d = _mk_definition(svc)

    run_id = _submit(svc, _req([AgentDefinitionRef(d.definition_id)]))

    run = svc.get_run(run_id, TENANT)
    assert len(run.agent_catalog) == 1
    entry = run.agent_catalog[0]
    assert entry.name == "reviewer"
    assert entry.kind == dm.AgentKind.BOT
    assert entry.engine == "openclaw"


def test_alias_becomes_the_catalog_name(db):
    svc = _service(db)
    d = _mk_definition(svc)

    run_id = _submit(svc, _req([AgentDefinitionRef(d.definition_id, "reviewer_strict")]))
    run = svc.get_run(run_id, TENANT)
    assert run.agent_catalog[0].name == "reviewer_strict"


def test_same_definition_twice_with_distinct_aliases(db):
    svc = _service(db)
    d = _mk_definition(svc)

    run_id = _submit(svc, _req([
        AgentDefinitionRef(d.definition_id, "reviewer_fast"),
        AgentDefinitionRef(d.definition_id, "reviewer_strict"),
    ]))
    names = [a.name for a in svc.get_run(run_id, TENANT).agent_catalog]
    assert names == ["reviewer_fast", "reviewer_strict"]


def test_duplicate_resolved_name_is_refused(db):
    svc = _service(db)
    d = _mk_definition(svc)
    with pytest.raises(ValueError):
        _submit(svc, _req([
            AgentDefinitionRef(d.definition_id),
            AgentDefinitionRef(d.definition_id),  # same name twice
        ]))


def test_unknown_reference_refuses_before_any_row(db):
    svc = _service(db)
    with pytest.raises(AgentReferenceUnresolved):
        _submit(svc, _req([AgentDefinitionRef(dm.AgentDefinitionId("nope"))]))
    assert svc.list_runs(TENANT).total == 0


def test_another_owners_definition_answers_like_unknown(db):
    svc = _service(db)
    bobs = _mk_definition(svc, owner=BOB)
    with pytest.raises(AgentReferenceUnresolved):
        _submit(svc, _req([AgentDefinitionRef(bobs.definition_id)]))
    assert svc.list_runs(TENANT).total == 0


def test_one_bad_reference_refuses_the_whole_submit(db):
    svc = _service(db)
    good = _mk_definition(svc)
    with pytest.raises(AgentReferenceUnresolved):
        _submit(svc, _req([
            AgentDefinitionRef(good.definition_id),
            AgentDefinitionRef(dm.AgentDefinitionId("missing")),
        ]))
    assert svc.list_runs(TENANT).total == 0


def test_deleted_definition_cannot_be_referenced(db):
    svc = _service(db)
    d = _mk_definition(svc)
    svc.delete_definition(d.definition_id, TENANT, ALICE)
    with pytest.raises(AgentReferenceUnresolved):
        _submit(svc, _req([AgentDefinitionRef(d.definition_id)]))


# ── provenance ──────────────────────────────────────────────────────────────

def test_provenance_is_recorded_beside_the_snapshot(db):
    svc = _service(db)
    d = _mk_definition(svc)
    run_id = _submit(svc, _req([AgentDefinitionRef(d.definition_id)]))

    refs = RunAgentRefRepository(db).get_for_run(dm.RunId(run_id), dm.TenantId(TENANT))
    assert refs["reviewer"].definition_id == d.definition_id
    assert refs["reviewer"].definition_row_id == d.row_id

    # The snapshot itself carries no definition keys (old readers decode it).
    import json
    from sqlalchemy import select
    with db.orm_session() as session:
        catalog = session.execute(
            select(om.AcAgentSwarmRunModel.agent_catalog).where(
                om.AcAgentSwarmRunModel.run_id == run_id
            )
        ).scalar_one()
    entry = json.loads(catalog)[0]
    assert "definition_id" not in entry
    assert "definition_row_id" not in entry


def test_inline_submit_writes_no_provenance(db):
    svc = _service(db)
    req = SubmitRunRequest(
        tenant=TENANT, goal="g", owner_id=ALICE, identity_bot_id=IDENTITY,
        agents=(dm.AgentSpec(name="hello_writer", role="hi",
                             kind=dm.AgentKind.BOT),),
    )
    run_id = _submit(svc, req)
    assert RunAgentRefRepository(db).get_for_run(
        dm.RunId(run_id), dm.TenantId(TENANT)
    ) == {}


def test_duplicate_idempotency_key_does_not_re_resolve(db):
    svc = _service(db)
    d = _mk_definition(svc)
    first = _submit(svc, _req([AgentDefinitionRef(d.definition_id)], key="k1"))

    # Revise the definition; the duplicate submit must still join the original.
    svc.update_definition(
        definition_id=d.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=d.row_id, name="reviewer", role="changed",
        kind=dm.AgentKind.BOT, engine="openclaw",
    )
    second = _submit(svc, _req([AgentDefinitionRef(d.definition_id)], key="k1"))

    assert second == first
    run = svc.get_run(first, TENANT)
    assert run.agent_catalog[0].role == "checks"  # original snapshot preserved


def test_resolver_refuses_a_request_carrying_both_roster_forms(db):
    """Belt-and-braces: the DTO refuses this, but the service must too."""
    svc = _service(db)
    d = _mk_definition(svc)
    req = SubmitRunRequest(
        tenant=TENANT, goal="g", owner_id=ALICE, identity_bot_id=IDENTITY,
        agents=(dm.AgentSpec(name="inline_one", role="r"),),
        agent_refs=(AgentDefinitionRef(d.definition_id),),
    )
    with pytest.raises(ValueError):
        svc._resolve_roster(req)
