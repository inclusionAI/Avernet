"""AgentDefinitionRepository: the version/tombstone model and owner isolation.

The version model is the baas_bot multi-version pattern: a version is a row,
an edit inserts a successor and retires the predecessor by writing the
predecessor's own ``id`` into ``is_deleted``. These tests pin the consequences
the specs depend on.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.repository import registry as om
from agentclaw.community.core.agentswarm.repository.agent_definition import (
    AgentDefinitionRepository, DefinitionConflictError,
)
from agentclaw.community.core.agentswarm.repository.run_agent_ref import (
    RunAgentRefRepository,
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


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    from agentclaw.community.core.base import Base
    Base.metadata.create_all(engine, tables=[
        om.AcAgentSwarmAgentDefinitionModel.__table__,
        om.AcAgentSwarmRunAgentRefModel.__table__,
    ])
    yield _TestDB(engine)
    engine.dispose()


@pytest.fixture()
def repo(db):
    return AgentDefinitionRepository(db)


@pytest.fixture()
def ref_repo(db):
    return RunAgentRefRepository(db)


TENANT = dm.TenantId("teamclaw")
ALICE = "49912"
BOB = "50087"


def _defn(definition_id="7c1e4b9a2f8d4e1f9a3b5c6d7e8f9a0b", *, name="reviewer",
          owner=ALICE, creator=None, modifier=None, role="checks things",
          manifest="", digest=""):
    return dm.AgentDefinition(
        definition_id=dm.AgentDefinitionId(definition_id),
        tenant=TENANT,
        owner_id=owner,
        creator_id=creator or owner,
        modifier_id=modifier or owner,
        name=name,
        role=role,
        manifest_document=manifest,
        manifest_digest=digest,
    )


# ── create / read round trip ─────────────────────────────────────────────────

def test_create_then_get_live_round_trip(repo):
    created = repo.create(_defn(role="original"))
    assert created.row_id > 0
    assert created.is_live

    got = repo.get_live(dm.AgentDefinitionId(created.definition_id), TENANT, ALICE)
    assert got is not None
    assert got.name == "reviewer"
    assert got.role == "original"
    assert got.owner_id == ALICE
    assert got.creator_id == ALICE
    assert got.modifier_id == ALICE


# ── version model ────────────────────────────────────────────────────────────

def test_replace_live_retires_predecessor_and_keeps_definition_id(repo):
    first = repo.create(_defn(role="v1"))
    successor = repo.replace_live(
        _defn(role="v2", creator=first.creator_id, modifier="50087"),
        expected_row_id=first.row_id,
    )

    # Identity is stable; the version handle is the row id.
    assert successor.definition_id == first.definition_id
    assert successor.row_id > first.row_id, "successor id must strictly increase"
    assert successor.role == "v2"
    # Authorship: creator copied forward, modifier is the editor.
    assert successor.creator_id == ALICE
    assert successor.modifier_id == "50087"

    # Exactly one live row, and the predecessor is retained as retired history.
    live = repo.get_live(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)
    assert live is not None and live.row_id == successor.row_id

    versions = repo.list_versions(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)
    assert [v.row_id for v in versions] == [successor.row_id, first.row_id]
    assert [v.is_live for v in versions] == [True, False]
    # The retired row keeps its own content and its own modifier.
    assert versions[1].role == "v1"
    assert versions[1].modifier_id == ALICE


def test_tombstone_value_equals_the_retired_rows_own_id(repo, db):
    first = repo.create(_defn(role="v1"))
    repo.replace_live(_defn(role="v2"), expected_row_id=first.row_id)

    with db.orm_session() as session:
        row = session.get(om.AcAgentSwarmAgentDefinitionModel, first.row_id)
        assert int(row.is_deleted) == first.row_id


def test_second_replace_on_already_replaced_row_conflicts(repo):
    first = repo.create(_defn(role="v1"))
    repo.replace_live(_defn(role="v2"), expected_row_id=first.row_id)

    with pytest.raises(DefinitionConflictError):
        repo.replace_live(_defn(role="v3"), expected_row_id=first.row_id)

    # The refused call wrote no version and left the winner live.
    versions = repo.list_versions(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)
    assert len(versions) == 2
    assert [v.role for v in versions if v.is_live] == ["v2"]


# ── delete / name reuse ──────────────────────────────────────────────────────

def test_retire_live_hides_from_live_reads_but_keeps_versions(repo):
    created = repo.create(_defn())
    definition_id = dm.AgentDefinitionId(created.definition_id)

    assert repo.retire_live(definition_id, TENANT, ALICE) == 1

    assert repo.get_live(definition_id, TENANT, ALICE) is None
    page = repo.list_live_page(TENANT, ALICE)
    assert page.total == 0
    # History remains readable.
    assert len(repo.list_versions(definition_id, TENANT, ALICE)) == 1


def test_name_can_be_reused_after_delete(repo):
    first = repo.create(_defn(name="reviewer"))
    repo.retire_live(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)

    second = repo.create(_defn(definition_id="freshid0000", name="reviewer"))
    assert second.definition_id != first.definition_id
    assert repo.get_live(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE) is None
    assert repo.get_live(dm.AgentDefinitionId(second.definition_id), TENANT, ALICE) is not None


def test_live_name_collision_is_refused(repo):
    repo.create(_defn(name="reviewer"))
    with pytest.raises(DefinitionConflictError):
        repo.create(_defn(definition_id="otherid", name="reviewer"))


def test_same_name_across_owners_is_allowed(repo):
    repo.create(_defn(name="reviewer", owner=ALICE))
    # A different owner may hold the same live name.
    created = repo.create(_defn(definition_id="bobid", name="reviewer", owner=BOB))
    assert created.owner_id == BOB


# ── owner isolation ─────────────────────────────────────────────────────────

def test_other_owner_sees_nothing(repo):
    created = repo.create(_defn())
    definition_id = dm.AgentDefinitionId(created.definition_id)

    assert repo.get_live(definition_id, TENANT, BOB) is None
    assert repo.get_row(created.row_id, TENANT, BOB) is None
    assert repo.get_many_live([definition_id], TENANT, BOB) == []
    assert repo.list_versions(definition_id, TENANT, BOB) == []
    assert repo.list_live_page(TENANT, BOB).total == 0
    # retire/replace by another owner mutate nothing.
    assert repo.retire_live(definition_id, TENANT, BOB) == 0
    with pytest.raises(DefinitionConflictError):
        repo.replace_live(_defn(role="hijack", owner=BOB), expected_row_id=created.row_id)
    assert repo.get_live(definition_id, TENANT, ALICE).role == "checks things"


def test_listing_paginates_deterministically(repo):
    for i in range(5):
        repo.create(_defn(definition_id=f"id{i:02d}", name=f"agent{i}"))

    p1 = repo.list_live_page(TENANT, ALICE, page=1, page_size=2)
    p2 = repo.list_live_page(TENANT, ALICE, page=2, page_size=2)
    p3 = repo.list_live_page(TENANT, ALICE, page=3, page_size=2)

    assert p1.total == 5
    seen = [d.definition_id for p in (p1, p2, p3) for d in p.items]
    assert len(seen) == 5
    assert len(set(seen)) == 5, "pages must not repeat an entry"


def test_name_filter_narrows_listing(repo):
    repo.create(_defn(definition_id="a1", name="reviewer"))
    repo.create(_defn(definition_id="a2", name="writer"))

    page = repo.list_live_page(TENANT, ALICE, name_filter="review")
    assert [d.name for d in page.items] == ["reviewer"]


# ── run provenance ──────────────────────────────────────────────────────────

def test_provenance_round_trip_and_run_isolation(ref_repo, repo):
    d = repo.create(_defn())
    run_a = dm.RunId("run-a")
    run_b = dm.RunId("run-b")

    ref_repo.replace_for_run(run_a, TENANT, [
        dm.RunAgentRef(run_a, TENANT, "reviewer",
                       dm.AgentDefinitionId(d.definition_id), d.row_id),
    ])
    ref_repo.replace_for_run(run_b, TENANT, [])

    got = ref_repo.get_for_run(run_a, TENANT)
    assert got["reviewer"].definition_row_id == d.row_id
    assert got["reviewer"].definition_id == d.definition_id
    assert ref_repo.get_for_run(run_b, TENANT) == {}


def test_provenance_write_is_idempotent(ref_repo, repo):
    d = repo.create(_defn())
    run = dm.RunId("run-a")
    rows = [dm.RunAgentRef(run, TENANT, "reviewer",
                           dm.AgentDefinitionId(d.definition_id), d.row_id)]

    ref_repo.replace_for_run(run, TENANT, rows)
    ref_repo.replace_for_run(run, TENANT, rows)  # a replay converges

    assert len(ref_repo.get_for_run(run, TENANT)) == 1


def test_get_many_live_with_no_ids_short_circuits(repo):
    assert repo.get_many_live([], TENANT, ALICE) == []


def test_replace_live_with_a_non_live_predecessor_refuses_and_writes_nothing(repo):
    """Retire-first means a missing predecessor aborts before the insert."""
    first = repo.create(_defn(role="v1"))

    with pytest.raises(DefinitionConflictError):
        repo.replace_live(_defn(role="v2"), expected_row_id=999999)

    # Nothing was written: still exactly one live row, still at v1.
    live = repo.get_live(dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)
    assert live.row_id == first.row_id and live.role == "v1"
    assert len(repo.list_versions(
        dm.AgentDefinitionId(first.definition_id), TENANT, ALICE)) == 1


def test_replace_live_translates_an_integrity_error_to_a_conflict(repo, monkeypatch):
    """The insert's own unique-key loss must surface as a conflict, not leak."""
    from sqlalchemy.exc import IntegrityError

    first = repo.create(_defn(role="v1"))

    real_session = repo._db.orm_session

    @contextmanager
    def _session_whose_flush_fails():
        with real_session() as session:
            def _boom():
                raise IntegrityError("insert", {}, Exception("uk_def_live"))
            session.flush = _boom
            yield session

    monkeypatch.setattr(repo._db, "orm_session", _session_whose_flush_fails)

    with pytest.raises(DefinitionConflictError):
        repo.replace_live(_defn(role="v2"), expected_row_id=first.row_id)
