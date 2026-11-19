"""End-to-end: definitions across their whole lifecycle, and both roster modes.

Covers the three integration behaviours the change promises:
reference-defined runs survive edits and deletes; inline and reference rosters
coexist in one session; and a run pins the version it captured.
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
    AgentDefinitionRef, SubmitRunRequest,
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
        om.AcAgentSwarmAgentAttemptModel.__table__,
        om.AcAgentSwarmMessageModel.__table__,
        om.AcAgentSwarmAgentResultModel.__table__,
        om.AcAgentSwarmRoundModel.__table__,
        om.AcAgentSwarmGuardEventModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


TENANT = "teamclaw"
ALICE = "49912"
IDENTITY = "20261117_dfpm4nls"


@pytest.fixture()
def svc(db):
    return AgentSwarmService(
        run_repo=RunRepository(db),
        dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db),
        attempt_repo=AgentAttemptRepository(db),
        config=AgentSwarmConfig(llm=LlmConfig(api_key="k", base_url="http://x")),
        definition_manifest_validator=_Validator(),
        definition_repo=AgentDefinitionRepository(db),
        run_agent_ref_repo=RunAgentRefRepository(db),
    )


def _mk(svc, name="hello_writer", role="v1"):
    return svc.create_definition(
        tenant=TENANT, owner_id=ALICE, name=name, role=role,
        kind=dm.AgentKind.BOT, engine="openclaw",
    )


def _submit(svc, req):
    return asyncio.run(svc.submit_async(req))


def _ref_req(d, *, key=None):
    return SubmitRunRequest(
        tenant=TENANT, goal="g", agents=(), owner_id=ALICE,
        idempotency_key=key, identity_bot_id=IDENTITY,
        agent_refs=(AgentDefinitionRef(d.definition_id),),
    )


def test_run_survives_definition_edit_and_delete(svc):
    d = _mk(svc)
    run_id = _submit(svc, _ref_req(d))

    svc.update_definition(
        definition_id=d.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=d.row_id, name="hello_writer", role="v2",
        kind=dm.AgentKind.BOT, engine="openclaw",
    )
    svc.delete_definition(d.definition_id, TENANT, ALICE)

    detail = svc.get_run_detail(run_id, TENANT)
    assert detail.agent_catalog[0].role == "v1"
    assert detail.agent_refs["hello_writer"].definition_row_id == d.row_id


def test_inline_and_reference_rosters_coexist(svc):
    d = _mk(svc)
    inline = _submit(svc, SubmitRunRequest(
        tenant=TENANT, goal="g", owner_id=ALICE, identity_bot_id=IDENTITY,
        agents=(dm.AgentSpec(name="hello_writer", role="inline",
                             kind=dm.AgentKind.BOT),),
    ))
    by_ref = _submit(svc, _ref_req(d))

    assert svc.get_run(inline, TENANT).agent_catalog[0].role == "inline"
    assert svc.get_run(by_ref, TENANT).agent_catalog[0].role == "v1"

    # Provenance only on the referenced run.
    assert svc.get_run_detail(inline, TENANT).agent_refs == {}
    assert svc.get_run_detail(by_ref, TENANT).agent_refs != {}


def test_versions_accumulate_while_identity_is_stable(svc):
    d = _mk(svc, role="v1")
    run_a = _submit(svc, _ref_req(d))

    d2 = svc.update_definition(
        definition_id=d.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=d.row_id, name="hello_writer", role="v2",
        kind=dm.AgentKind.BOT, engine="openclaw",
    )
    d3 = svc.update_definition(
        definition_id=d.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=d2.row_id, name="hello_writer", role="v3",
        kind=dm.AgentKind.BOT, engine="openclaw",
    )

    versions = svc.list_definition_versions(d.definition_id, TENANT, ALICE)
    assert [v.role for v in versions] == ["v3", "v2", "v1"]
    assert {v.definition_id for v in versions} == {d.definition_id}
    assert [v.is_live for v in versions] == [True, False, False]

    # The first run still cites the version it captured; a new run takes v3.
    assert svc.get_run_detail(run_a, TENANT).agent_refs[
        "hello_writer"].definition_row_id == d.row_id
    run_b = _submit(svc, _ref_req(d))
    assert svc.get_run_detail(run_b, TENANT).agent_refs[
        "hello_writer"].definition_row_id == d3.row_id
