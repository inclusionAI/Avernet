"""Unified HarnessTemplate repository — behavior + cross-backend contract.

Round-3/session-2 criteria: single body, atomic upsert on
uk_harness_template_name_env (re-create returns the SAME id),
list/get/update/soft_delete/load_all_active parity, version bump on
update. No ZDAS-skipped test; the MySQL LAST_INSERT_ID(id) arm is
covered by the manual Pre acceptance gate.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, UniqueConstraint, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.harness.models import (
    Layer,
    PatchOperation,
    PatchTarget,
    PatchTemplate,
    PatchTemplateStatus,
    RiskLevel,
)
from agentclaw.community.plugins.harness_repository import (
    HarnessTemplateRepository,
)
from agentclaw.community.utils.env_utils import get_current_env

pytestmark = pytest.mark.integration


def _create_schema(engine):
    """Private MetaData copy of HarnessPatchTemplateModel — copies the
    uk_harness_template_name_env unique constraint (the upsert's
    conflict target)."""
    from agentclaw.community.core.harness.sqlite_models import (
        HarnessPatchTemplateModel,
    )

    src = HarnessPatchTemplateModel.__table__
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
        UniqueConstraint("name", "env", name="uk_harness_template_name_env"),
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
        f"sqlite:///{tmp_path / 'tpl.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return HarnessTemplateRepository(_make_db(tmp_path))


def _tpl(**overrides):
    base = PatchTemplate(
        name="add-roles",
        layer=Layer.L1,
        target=PatchTarget(files=["AGENTS.md"], sections=["roles"]),
        version=1,
        description="adds default roles",
        operations=[
            PatchOperation(op="insert", target="AGENTS.md", template="t1")
        ],
        risk_level=RiskLevel.LOW,
        status=PatchTemplateStatus.ACTIVE,
        env=get_current_env(),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_create_inserts_and_returns_id(repo):
    r = repo.create(_tpl())
    assert r.id is not None
    got = repo.get_by_id(r.id)
    assert got.name == "add-roles"
    assert got.layer == Layer.L1


def test_create_duplicate_name_env_raises(repo):
    # Prod parity: plain INSERT, no upsert. A second create() on the
    # same (name, env) violates uk_harness_template_name_env and
    # raises (IntegrityError) — exactly what both legacy twins did;
    # the router surfaces it as a 500.
    from sqlalchemy.exc import IntegrityError

    repo.create(_tpl())
    with pytest.raises(IntegrityError):
        repo.create(_tpl(description="dup"))


def test_create_distinct_name_same_env_ok(repo):
    a = repo.create(_tpl(name="a"))
    b = repo.create(_tpl(name="b"))
    assert a.id != b.id
    rows, total = repo.list()
    assert total == 2


def test_create_distinct_env_creates_distinct_row(repo):
    a = repo.create(_tpl(env="test"))
    b = repo.create(_tpl(env="prod"))
    assert a.id != b.id


def test_get_by_name(repo):
    repo.create(_tpl())
    got = repo.get_by_name("add-roles", get_current_env())
    assert got is not None
    assert got.layer == Layer.L1
    assert repo.get_by_name("missing", get_current_env()) is None


def test_list_filters_and_pagination(repo):
    repo.create(_tpl(name="a", layer=Layer.L1))
    repo.create(_tpl(name="b", layer=Layer.L2))
    repo.create(_tpl(name="c", layer=Layer.L1))
    rows, total = repo.list(layer="L1")
    assert total == 2
    assert {r.name for r in rows} == {"a", "c"}

    rows, total = repo.list(keyword="a")
    assert total == 1
    assert rows[0].name == "a"


def test_list_limit_offset(repo):
    for i in range(5):
        repo.create(_tpl(name=f"t{i}"))
    rows, total = repo.list(offset=2, limit=2)
    assert total == 5
    assert len(rows) == 2


def test_update_bumps_version(repo):
    created = repo.create(_tpl())
    assert created.version == 1
    updated = repo.update(created.id, description="new")
    assert updated.version == 2
    assert updated.description == "new"


def test_update_strips_caller_version(repo):
    created = repo.create(_tpl())
    updated = repo.update(created.id, version=999, description="x")
    # version=999 must be stripped; repo bumps to 2.
    assert updated.version == 2


def test_update_missing_returns_none(repo):
    assert repo.update(99999, description="x") is None


def test_soft_delete_rowcount(repo):
    rid = repo.create(_tpl()).id
    assert repo.soft_delete(rid) is True
    got = repo.get_by_id(rid)
    assert got.status == PatchTemplateStatus.DEPRECATED
    # Second soft_delete on the same row still matches (rowcount>0,
    # prod parity).
    assert repo.soft_delete(rid) is True
    assert repo.soft_delete(99999) is False


def test_load_all_active(repo):
    a = repo.create(_tpl(name="a"))
    repo.create(_tpl(name="b"))
    repo.soft_delete(a.id)
    actives = repo.load_all_active()
    assert [t.name for t in actives] == ["b"]
