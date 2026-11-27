"""AgentSwarmService definition CRUD: ownership, versioning, refusals.

Every method carries the caller's owner id. A definition owned by someone else
is reported as absent rather than forbidden, so the id space cannot be probed.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
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
from agentclaw.community.core.agentswarm.repository.run_dispatch import (
    RunDispatchRepository,
)
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError, AgentSwarmRunNotFoundError, DefinitionManifestInvalid,
    DefinitionNameConflict, DefinitionRowConflict,
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
    """Records calls; optionally rejects like the platform preflight."""

    def __init__(self, reject: bool = False):
        self.reject = reject
        self.seen: list[str] = []

    def validate_document(self, document: str) -> None:
        self.seen.append(document)
        if self.reject:
            raise DefinitionManifestInvalid("nope")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        om.AcAgentSwarmAgentDefinitionModel.__table__,
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


def _service(db, validator=None) -> AgentSwarmService:
    return AgentSwarmService(
        run_repo=RunRepository(db),
        dispatch_repo=RunDispatchRepository(db),
        agent_repo=AgentRepository(db),
        message_repo=MessageRepository(db),
        round_repo=RoundRepository(db),
        guard_repo=GuardEventRepository(db),
        result_repo=AgentResultRepository(db),
        attempt_repo=AgentAttemptRepository(db),
        config=AgentSwarmConfig(),
        definition_manifest_validator=validator or _Validator(),
        definition_repo=AgentDefinitionRepository(db),
    )


TENANT = "teamclaw"
ALICE = "49912"
BOB = "50087"


def _create(svc, *, owner=ALICE, name="reviewer", role="checks things", **kw):
    return svc.create_definition(
        tenant=TENANT, owner_id=owner, name=name, role=role, **kw,
    )


# ── create / authorship ─────────────────────────────────────────────────────

def test_create_records_caller_as_owner_author_and_editor(db):
    svc = _service(db)
    d = _create(svc)
    assert d.owner_id == ALICE
    assert d.creator_id == ALICE
    assert d.modifier_id == ALICE
    assert d.row_id > 0


def test_name_collision_raises_typed_conflict(db):
    svc = _service(db)
    _create(svc, name="reviewer")
    with pytest.raises(DefinitionNameConflict):
        _create(svc, name="reviewer")


# ── ownership ───────────────────────────────────────────────────────────────

def test_other_owner_gets_not_found(db):
    svc = _service(db)
    d = _create(svc)

    assert svc.get_definition(d.definition_id, TENANT, BOB) is None
    assert svc.list_definition_versions(d.definition_id, TENANT, BOB) == []
    assert svc.list_definitions(TENANT, BOB).total == 0
    assert svc.delete_definition(d.definition_id, TENANT, BOB) == 0
    with pytest.raises(AgentSwarmRunNotFoundError):
        svc.update_definition(
            definition_id=d.definition_id, tenant=TENANT, owner_id=BOB,
            expected_row_id=d.row_id, name="hijack",
        )
    # Alice's definition is untouched.
    assert svc.get_definition(d.definition_id, TENANT, ALICE).name == "reviewer"


# ── versioning ──────────────────────────────────────────────────────────────

def test_update_keeps_definition_id_and_copies_creator(db):
    svc = _service(db)
    first = _create(svc, role="v1")

    second = svc.update_definition(
        definition_id=first.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=first.row_id, name="reviewer", role="v2",
    )

    assert second.definition_id == first.definition_id
    assert second.row_id > first.row_id
    assert second.role == "v2"
    assert second.creator_id == ALICE     # author unchanged
    assert second.modifier_id == ALICE    # editor is the caller

    versions = svc.list_definition_versions(first.definition_id, TENANT, ALICE)
    assert [v.is_live for v in versions] == [True, False]


def test_stale_row_id_is_refused_and_creates_no_version(db):
    svc = _service(db)
    first = _create(svc, role="v1")
    svc.update_definition(
        definition_id=first.definition_id, tenant=TENANT, owner_id=ALICE,
        expected_row_id=first.row_id, name="reviewer", role="v2",
    )

    with pytest.raises(DefinitionRowConflict):
        svc.update_definition(
            definition_id=first.definition_id, tenant=TENANT, owner_id=ALICE,
            expected_row_id=first.row_id, name="reviewer", role="v3",
        )

    versions = svc.list_definition_versions(first.definition_id, TENANT, ALICE)
    assert len(versions) == 2
    assert [v.role for v in versions if v.is_live] == ["v2"]


def test_delete_hides_from_live_and_lists_versions(db):
    svc = _service(db)
    d = _create(svc)
    assert svc.delete_definition(d.definition_id, TENANT, ALICE) == 1
    assert svc.get_definition(d.definition_id, TENANT, ALICE) is None
    assert svc.list_definitions(TENANT, ALICE).total == 0
    assert len(svc.list_definition_versions(d.definition_id, TENANT, ALICE)) == 1


def test_name_can_be_reused_after_delete(db):
    svc = _service(db)
    first = _create(svc, name="reviewer")
    svc.delete_definition(first.definition_id, TENANT, ALICE)
    second = _create(svc, name="reviewer")
    assert second.definition_id != first.definition_id


# ── manifest validation ─────────────────────────────────────────────────────

def test_manifest_is_validated_at_write_and_stored_verbatim(db):
    validator = _Validator()
    svc = _service(db, validator)
    doc = "schema_version: 1\nscript:\n  body: \"echo $(id)\"\n"
    d = _create(svc, manifest_document=doc)

    assert validator.seen == [doc]
    assert d.manifest_document == doc
    assert d.manifest_digest.startswith("sha256:")
    assert len(d.manifest_digest) == len("sha256:") + 64


def test_rejected_manifest_persists_nothing(db):
    svc = _service(db, _Validator(reject=True))
    with pytest.raises(DefinitionManifestInvalid):
        _create(svc, manifest_document="bad: [")
    assert svc.list_definitions(TENANT, ALICE).total == 0


def test_blank_manifest_is_stored_as_the_synthesized_document(db):
    # Blank is a valid *input* (provisioning would synthesize from 指令), but the
    # library stores what that persona is: a document whose SOUL.md carries the
    # instructions. Storing blank left every reader re-synthesizing it.
    svc = _service(db)
    d = _create(svc, manifest_document="", instructions="只回复一个词：hello")
    assert "SOUL.md" in d.manifest_document
    assert "只回复一个词：hello" in d.manifest_document
    assert d.manifest_digest.startswith("sha256:")


def test_a_supplied_manifest_is_stored_verbatim(db):
    svc = _service(db)
    doc = "schema_version: 1\nmanifest:\n  identity:\n    - type: MEMORY.md\n      content: kept\n"
    d = _create(svc, manifest_document=doc)
    assert d.manifest_document == doc


def test_unbound_validator_refuses_writes(db):
    svc = _service(db, validator=None)
    svc._definition_manifest_validator = None
    with pytest.raises(AgentSwarmConfigError):
        _create(svc)


# ── role limit ──────────────────────────────────────────────────────────────

def test_over_long_role_is_refused(db):
    svc = _service(db)
    with pytest.raises(ValueError):
        _create(svc, role="x" * 513)


def test_role_at_limit_is_accepted(db):
    svc = _service(db)
    role = "x" * 512
    assert _create(svc, role=role).role == role


def test_replace_conflict_from_the_repository_is_translated(db):
    """A live-slot loss on revise surfaces as DefinitionRowConflict."""
    from agentclaw.community.core.agentswarm.repository.agent_definition.impl import (
        DefinitionConflictError,
    )

    svc = _service(db)
    first = _create(svc, role="v1")

    def _boom(*a, **kw):
        raise DefinitionConflictError("uk_def_live")

    svc._definition_repo.replace_live = _boom
    with pytest.raises(DefinitionRowConflict):
        svc.update_definition(
            definition_id=first.definition_id, tenant=TENANT, owner_id=ALICE,
            expected_row_id=first.row_id, name="reviewer", role="v2",
        )
