"""The genome repositories against SQLite: idempotent records, ``seq``, CAS moves, promotions, DDL/ORM agreement."""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_genome.repository import models as genome_models
from agentclaw.community.core.bot_genome.repository.models import (
    GenomePatchRow,
    GenomePromotionRow,
    GenomeRevisionRow,
)
from agentclaw.community.core.repository.implementations.bot.genome import (
    SEQ_RETRIES,
    GenomePatchRepository,
    GenomePromotionRepository,
    GenomeRefRepository,
    GenomeRevisionRepository,
)

BOT = dict(env="dev", entity_id="ent_a", bot_id="bot_1")
OTHER = dict(env="dev", entity_id="ent_b", bot_id="bot_2")
R1, R2, R3 = ("sha256:" + c * 64 for c in "abc")
NOW = datetime(2026, 10, 19, 12, 0, 0)


class InMemorySqliteDB:
    def __init__(self, engine):
        self._session_factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return InMemorySqliteDB(engine)


def _revision(revision_id: str = R1, **overrides) -> GenomeRevisionRow:
    base = dict(
        **BOT, revision_id=revision_id, parent_id=None, genome_schema=1, status="draft",
        created_kind="user", created_actor="u1", content='{"policy":{},"spec":{}}', created_at=NOW,
    )
    base.update(overrides)
    return GenomeRevisionRow(**base)


def _stamp(**overrides):
    base = dict(moved_kind="user", moved_actor="u1", run_id=None, reason=None, moved_at=NOW)
    base.update(overrides)
    return base


# -- revisions -------------------------------------------------------------------------------------


def test_insert_assigns_seq_and_is_idempotent_by_revision_id(db):
    repo = GenomeRevisionRepository(db)
    first, created = repo.insert(_revision(R1))
    assert created and first.seq == 1 and first.id is not None
    second, created = repo.insert(_revision(R2, parent_id=R1))
    assert created and second.seq == 2 and second.parent_id == R1
    again, created = repo.insert(_revision(R1, parent_id=R2, status="candidate"))
    assert not created and again.seq == 1 and again.parent_id is None and again.status == "draft"
    assert repo.get(**BOT, revision_id=R1).id == first.id
    assert repo.get_by_seq(**BOT, seq=2).revision_id == R2
    assert repo.get(**OTHER, revision_id=R1) is None and repo.get_by_seq(**OTHER, seq=1) is None


def test_seq_is_per_bot_and_a_seq_race_retries(db, monkeypatch):
    repo = GenomeRevisionRepository(db)
    repo.insert(_revision(R1))
    other, _ = repo.insert(GenomeRevisionRow(**{**_revision(R1).model_dump(), **OTHER}))
    assert other.seq == 1
    stale = [1]  # the first attempt reads a stale MAX(seq) and collides on uk_bot_seq

    real = GenomeRevisionRepository._next_seq

    def racing(session, **axes):
        if stale:
            stale.pop()
            return 1
        return real(session, **axes)

    monkeypatch.setattr(GenomeRevisionRepository, "_next_seq", staticmethod(racing))
    row, created = repo.insert(_revision(R2))
    assert created and row.seq == 2


def test_a_seq_race_that_never_resolves_is_an_error(db, monkeypatch):
    repo = GenomeRevisionRepository(db)
    repo.insert(_revision(R1))
    monkeypatch.setattr(GenomeRevisionRepository, "_next_seq", staticmethod(lambda session, **axes: 1))
    with pytest.raises(RuntimeError, match=str(SEQ_RETRIES)):
        repo.insert(_revision(R2))


def test_list_filters_and_pages_newest_seq_first(db):
    repo = GenomeRevisionRepository(db)
    repo.insert(_revision(R1))
    repo.insert(_revision(R2, parent_id=R1, status="candidate"))
    repo.insert(_revision(R3, parent_id=R1, status="candidate"))
    total, rows = repo.list(**BOT)
    assert total == 3 and [r.seq for r in rows] == [3, 2, 1]
    total, rows = repo.list(**BOT, status="candidate", page=1, page_size=1)
    assert total == 2 and [r.revision_id for r in rows] == [R3]
    total, rows = repo.list(**BOT, parent_id=R1, page=2, page_size=1)
    assert total == 2 and [r.revision_id for r in rows] == [R2]
    assert repo.list(**OTHER) == (0, [])
    with pytest.raises(ValueError):
        repo.list(**BOT, page=0)


def test_update_metadata_changes_only_what_is_given(db):
    repo = GenomeRevisionRepository(db)
    repo.insert(_revision(R1))
    row = repo.update_metadata(**BOT, revision_id=R1, status="promoted")
    assert (row.status, row.evaluations, row.annotations) == ("promoted", "[]", "{}")
    row = repo.update_metadata(**BOT, revision_id=R1, evaluations='["eval_1"]', annotations='{"note":"x"}')
    assert (row.status, row.evaluations, row.annotations) == ("promoted", '["eval_1"]', '{"note":"x"}')
    assert repo.update_metadata(**OTHER, revision_id=R1, status="archived") is None


# -- refs ------------------------------------------------------------------------------------------


def test_move_is_compare_and_swap_with_an_event_per_move(db):
    repo = GenomeRefRepository(db)
    created = repo.move(**BOT, name="draft", revision_id=R1, expected_revision_id=None, **_stamp())
    assert created.version == 1 and created.revision_id == R1
    assert repo.move(**BOT, name="draft", revision_id=R2, expected_revision_id=None, **_stamp()) is None
    assert repo.move(**BOT, name="draft", revision_id=R2, expected_revision_id=R3, **_stamp()) is None
    moved = repo.move(**BOT, name="draft", revision_id=R2, expected_revision_id=R1, **_stamp(reason="go", moved_at=NOW + timedelta(seconds=1)))
    assert (moved.version, moved.revision_id, moved.reason) == (2, R2, "go")
    assert repo.get(**BOT, name="draft").revision_id == R2 and repo.get(**OTHER, name="draft") is None
    events = repo.events(**BOT, name="draft")
    assert [(e.from_revision_id, e.to_revision_id) for e in events] == [(R1, R2), (None, R1)]
    assert repo.move(**BOT, name="draft", revision_id=R3, expected_revision_id=None, **_stamp()) is None
    assert len(repo.events(**BOT, name="draft")) == 2, "a refused move leaves no event"


def test_move_pair_moves_active_and_previous_in_one_transaction(db):
    repo = GenomeRefRepository(db)
    active, previous = repo.move_pair(**BOT, revision_id=R1, expected_active=None, **_stamp())
    assert active.revision_id == R1 and previous is None
    assert repo.move_pair(**BOT, revision_id=R2, expected_active=R3, **_stamp()) is None
    active, previous = repo.move_pair(**BOT, revision_id=R2, expected_active=R1, **_stamp())
    assert (active.revision_id, active.version, previous.revision_id, previous.version) == (R2, 2, R1, 1)
    active, previous = repo.move_pair(**BOT, revision_id=R3, expected_active=R2, **_stamp())
    assert (active.revision_id, previous.revision_id, previous.version) == (R3, R2, 2)
    assert [r.name for r in repo.list(**BOT)] == ["active", "previous"]
    assert [(e.from_revision_id, e.to_revision_id) for e in repo.events(**BOT, name="previous")] == [(R1, R2), (None, R1)]


def test_candidate_refs_are_counted_per_run_and_listed_by_prefix(db):
    repo = GenomeRefRepository(db)
    for n, rev in ((1, R1), (2, R2)):
        repo.move(**BOT, name=f"candidate/run_7/{n}", revision_id=rev, expected_revision_id=None, **_stamp(moved_kind="strategy_run", run_id="run_7"))
    repo.move(**BOT, name="candidate/run_8/1", revision_id=R3, expected_revision_id=None, **_stamp(run_id="run_8"))
    assert repo.candidate_count(**BOT, run_id="run_7") == 2
    assert repo.candidate_count(**BOT, run_id="run_9") == 0
    assert [r.name for r in repo.list(**BOT, name_prefix="candidate/run_7/")] == ["candidate/run_7/1", "candidate/run_7/2"]


# -- patches and promotions ------------------------------------------------------------------------


def test_patch_rows_are_content_addressed(db):
    repo = GenomePatchRepository(db)
    row = GenomePatchRow(**BOT, digest=R2, base_id=R1, content='{"ops":[]}', created_at=NOW)
    first, created = repo.insert(row)
    again, created_again = repo.insert(row.model_copy(update={"base_id": R3}))
    assert created and not created_again and again.id == first.id and again.base_id == R1
    assert repo.get(**BOT, digest=R2).content == '{"ops":[]}' and repo.get(**OTHER, digest=R2) is None


def test_promotions_round_trip_list_and_join_on_the_apply_reference(db):
    repo = GenomePromotionRepository(db)

    def _promotion(promotion_id: str, created_at: datetime) -> GenomePromotionRow:
        return GenomePromotionRow(
            **BOT, promotion_id=promotion_id, ref="active", revision_id=R2, from_revision_id=R1,
            document_digest=R3, document_json='{"manifest":{}}', going_back=False, actor_kind="user",
            actor_id="u1", reason="ship", status="applying", apply_kind="pending", created_at=created_at,
        )

    first = repo.insert(_promotion("a" * 32, NOW))
    second = repo.insert(_promotion("b" * 32, NOW + timedelta(seconds=1)))
    assert first.id is not None and first.document_json == '{"manifest":{}}' and first.apply_detail == ""
    total, rows = repo.list(**BOT)
    assert total == 2 and [r.promotion_id for r in rows] == [second.promotion_id, first.promotion_id]
    assert repo.list(**OTHER) == (0, [])
    finished = repo.finish(**BOT, promotion_id=first.promotion_id, status="applied", apply_reference="apply_9", apply_detail="ok")
    assert (finished.status, finished.apply_reference, finished.apply_detail) == ("applied", "apply_9", "ok")
    assert repo.by_apply_reference(**BOT, apply_reference="apply_9").promotion_id == first.promotion_id
    assert repo.by_apply_reference(**BOT, apply_reference="apply_0") is None
    assert repo.get(**OTHER, promotion_id=first.promotion_id) is None
    assert repo.finish(**BOT, promotion_id="c" * 32, status="applied", apply_reference=None, apply_detail="") is None


# -- the DDL and the ORM agree ---------------------------------------------------------------------

_DDL = Path(genome_models.__file__).resolve().parents[1] / "sql" / "2026_10_19_bot_genome.sql"


def _ddl_columns() -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    current = None
    for line in _DDL.read_text(encoding="utf-8").splitlines():
        table = re.match(r"CREATE TABLE `(\w+)`", line)
        if table:
            current = tables.setdefault(table.group(1), [])
            continue
        column = re.match(r"\s+`(\w+)`\s", line)
        if current is not None and column and not line.lstrip().startswith(("PRIMARY", "UNIQUE", "KEY")):
            current.append(column.group(1))
    return tables


@pytest.mark.parametrize(
    "model",
    [
        genome_models.GenomeRevisionModel,
        genome_models.GenomeRefModel,
        genome_models.GenomeRefEventModel,
        genome_models.GenomePatchModel,
        genome_models.GenomePromotionModel,
    ],
    ids=lambda m: m.__tablename__,
)
def test_ddl_columns_match_the_orm(model) -> None:
    ddl = _ddl_columns()[model.__tablename__]
    orm = [column.name for column in model.__table__.columns]
    assert sorted(ddl) == sorted(orm), f"{model.__tablename__}: DDL {ddl} vs ORM {orm}"


# -- review: transactions and LIKE escaping -------------------------------------------------------


class AutocommitSqliteDB(InMemorySqliteDB):
    """The production shape: ``orm_session`` commits every statement; ``transactional_orm_session`` is the real transaction."""

    def __init__(self, engine):
        super().__init__(engine)
        self._autocommit = sessionmaker(bind=engine.execution_options(isolation_level="AUTOCOMMIT"), autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._autocommit()
        try:
            yield db
        finally:
            db.close()

    transactional_orm_session = InMemorySqliteDB.orm_session


def test_a_lost_pair_move_leaves_nothing_behind_under_autocommit(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    repo = GenomeRefRepository(AutocommitSqliteDB(engine))
    repo.move_pair(**BOT, revision_id=R1, expected_active=None, **_stamp())
    repo.move_pair(**BOT, revision_id=R2, expected_active=R1, **_stamp())
    # The "previous" CAS loses: the read of previous is stale (a concurrent mover changed it), so
    # its conditional UPDATE matches nothing after the active UPDATE already ran.
    real_row = GenomeRefRepository._row

    def stale_previous(self, session, **kw):
        row = real_row(self, session, **kw)
        return SimpleNamespace(revision_id=R3) if kw["name"] == "previous" else row

    monkeypatch.setattr(GenomeRefRepository, "_row", stale_previous)
    assert repo.move_pair(**BOT, revision_id=R3, expected_active=R2, **_stamp()) is None
    monkeypatch.undo()
    assert repo.get(**BOT, name="active").revision_id == R2, "active did not move when previous could not"
    assert [(e.from_revision_id, e.to_revision_id) for e in repo.events(**BOT, name="active")] == [(R1, R2), (None, R1)]


def test_candidate_prefixes_are_matched_literally(db):
    repo = GenomeRefRepository(db)
    repo.move(**BOT, name="candidate/run_7/1", revision_id=R1, expected_revision_id=None, **_stamp())
    repo.move(**BOT, name="candidate/runX7/1", revision_id=R2, expected_revision_id=None, **_stamp())
    assert repo.candidate_count(**BOT, run_id="run_7") == 1
    assert [r.name for r in repo.list(**BOT, name_prefix="candidate/run_7/")] == ["candidate/run_7/1"]
