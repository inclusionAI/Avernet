"""Unified HarnessPatchRecord repository — behavior + cross-backend contract.

Round-3/session-2 criteria: single body, 5-method Protocol parity.
The off-Protocol ``update_preview`` carried by both legacy twins is
dropped — no production caller existed. No ZDAS-skipped test; prod
round-trip is the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.harness.models import (
    Layer,
    PatchOperation,
    PatchRecord,
    PatchStatus,
    PatchTarget,
)
from agentclaw.community.core.repository.implementations.harness.patch_record import HarnessPatchRecordRepository
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


def _create_schema(engine):
    from agentclaw.community.core.harness.sqlite_models import HarnessPatchRecordModel

    src = HarnessPatchRecordModel.__table__
    md = MetaData()
    Table(
        src.name,
        md,
        *[
            Column(
                c.name,
                c.type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                autoincrement=c.autoincrement,
                server_default=c.server_default.arg
                if c.server_default is not None
                else None,
            )
            for c in src.columns
        ],
    )
    md.create_all(engine)


class _FileSqliteDB:
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

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


def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'hpr.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return HarnessPatchRecordRepository(_make_db(tmp_path))


def _record(**overrides):
    base = PatchRecord(
        bot_id="bot-1",
        entity_id="ent-1",
        patch_id=100,
        layer=Layer.L1,
        target=PatchTarget(files=["AGENTS.md"], sections=["roles"]),
        status=PatchStatus.PLANNED,
        operations=[
            PatchOperation(op="insert", target="AGENTS.md", template="t1")
        ],
        applied_by="alice",
        env=get_current_env(),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_create_returns_id_and_round_trip(repo):
    rid = repo.create(_record())
    assert isinstance(rid, int)
    got = repo.get_by_id(rid)
    assert got is not None
    assert got.id == rid
    assert got.bot_id == "bot-1"
    assert got.layer == Layer.L1
    assert got.target.files == ["AGENTS.md"]
    assert len(got.operations) == 1
    assert got.operations[0].op == "insert"


def test_get_by_id_missing(repo):
    assert repo.get_by_id(99999) is None


def test_list_by_bot(repo):
    rid1 = repo.create(_record(patch_id=1))
    rid2 = repo.create(_record(patch_id=2))
    rows = repo.list_by_bot("bot-1", "ent-1")
    ids = [r.id for r in rows]
    assert set(ids) == {rid1, rid2}


def test_list_by_bot_filters_status(repo):
    rid1 = repo.create(_record(patch_id=1, status=PatchStatus.PLANNED))
    repo.create(_record(patch_id=2, status=PatchStatus.APPLIED))
    rows = repo.list_by_bot("bot-1", "ent-1", status="planned")
    assert [r.id for r in rows] == [rid1]


def test_get_by_patch_id_returns_latest(repo):
    repo.create(_record(patch_id=42, status=PatchStatus.PLANNED))
    rid2 = repo.create(_record(patch_id=42, status=PatchStatus.APPLIED))
    got = repo.get_by_patch_id(42)
    assert got is not None
    assert got.id == rid2
    assert got.status == PatchStatus.APPLIED


def test_update_status(repo):
    rid = repo.create(_record())
    repo.update_status(rid, PatchStatus.APPLIED)
    assert repo.get_by_id(rid).status == PatchStatus.APPLIED


def test_update_status_with_failed_reason(repo):
    rid = repo.create(_record())
    repo.update_status(rid, PatchStatus.FAILED, failed_reason="boom")
    got = repo.get_by_id(rid)
    assert got.status == PatchStatus.FAILED
    assert got.failed_reason == "boom"


def test_update_status_missing_is_noop(repo):
    # Mirror local twin: silent return when row absent.
    repo.update_status(99999, PatchStatus.APPLIED)  # must not raise
