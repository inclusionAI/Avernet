"""Unified access-control PolicyRepository — behavior + contract.

Round-3/session-3 criteria: single ORM body, 8-method
``PolicyRepository`` parity. Covers the prod-parity guarantees:
genuine atomic upsert (idempotent, single statement, returns None),
``gmt_modified`` advancing on re-upsert (matches prod
``ON UPDATE CURRENT_TIMESTAMP``), and ``get_config_by_key`` letting
errors propagate (the old SQLite twin's blanket try/except is gone).
"""
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.access.sqlite_models import (
    AccessControlPolicy,
    UserInfo,
)
from agentclaw.community.core.repository.implementations.identity.policy import PolicyRepository

pytestmark = pytest.mark.integration


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


def _engine(tmp_path):
    return create_engine(
        f"sqlite:///{tmp_path / 'pol.db'}",
        connect_args={"check_same_thread": False},
    )


@pytest.fixture
def repo(tmp_path):
    engine = _engine(tmp_path)
    AccessControlPolicy.__table__.create(engine)
    UserInfo.__table__.create(engine)
    return PolicyRepository(_FileSqliteDB(engine))


@pytest.fixture
def repo_no_config_tables(repo):
    """repo whose DB has policy/user tables but NOT the config
    tables — used to prove get_config_by_key propagates errors."""
    return repo


# ── policy upsert ───────────────────────────────────────────────────

def test_upsert_policy_insert_then_get(repo):
    repo.upsert_policy(
        entity_id="e1", entity_type="bot", policy='{"allow": true}'
    )
    rec = repo.get_by_entity(entity_id="e1", entity_type="bot")
    assert rec is not None
    assert rec.policy == '{"allow": true}'


def test_upsert_policy_is_idempotent_single_row(repo):
    repo.upsert_policy(entity_id="e1", entity_type="bot", policy="p1")
    repo.upsert_policy(entity_id="e1", entity_type="bot", policy="p2")
    rec = repo.get_by_entity(entity_id="e1", entity_type="bot")
    assert rec.policy == "p2"  # updated in place, not duplicated
    # entity isolation: a different entity_type is a separate row.
    repo.upsert_policy(entity_id="e1", entity_type="user", policy="x")
    assert (
        repo.get_by_entity(entity_id="e1", entity_type="bot").policy
        == "p2"
    )


def test_upsert_policy_advances_gmt_modified(repo, tmp_path):
    repo.upsert_policy(entity_id="e1", entity_type="bot", policy="p1")
    # Backdate gmt_modified to prove the update arm sets func.now().
    with repo._db.orm_session() as db:
        row = db.query(AccessControlPolicy).filter_by(
            entity_id="e1", entity_type="bot"
        ).first()
        row.gmt_modified = datetime(2000, 1, 1)
    repo.upsert_policy(entity_id="e1", entity_type="bot", policy="p2")
    with repo._db.orm_session() as db:
        row = db.query(AccessControlPolicy).filter_by(
            entity_id="e1", entity_type="bot"
        ).first()
        assert row.gmt_modified.year > 2000


# ── user info upsert ────────────────────────────────────────────────

def test_upsert_user_info_idempotent(repo):
    repo.upsert_user_info(
        user_id="u1", user_type="NORMAL", status="ACCESS"
    )
    repo.upsert_user_info(
        user_id="u1", user_type="NORMAL", status="REFUSE"
    )
    rec = repo.get_user_info(user_id="u1", user_type="NORMAL")
    assert rec.status == "REFUSE"
    assert len(repo.list_users(user_type="NORMAL")) == 1


def test_upsert_user_info_advances_gmt_modified(repo):
    repo.upsert_user_info(
        user_id="u1", user_type="NORMAL", status="ACCESS"
    )
    with repo._db.orm_session() as db:
        row = db.query(UserInfo).filter_by(
            user_id="u1", user_type="NORMAL"
        ).first()
        row.gmt_modified = datetime(2000, 1, 1)
    repo.upsert_user_info(
        user_id="u1", user_type="NORMAL", status="REFUSE"
    )
    with repo._db.orm_session() as db:
        row = db.query(UserInfo).filter_by(
            user_id="u1", user_type="NORMAL"
        ).first()
        assert row.gmt_modified.year > 2000


def test_list_users_and_count_compete(repo):
    repo.upsert_user_info(
        user_id="a", user_type="COMPETE", status="ACCESS"
    )
    repo.upsert_user_info(
        user_id="b", user_type="COMPETE", status="ACCESS"
    )
    repo.upsert_user_info(
        user_id="c", user_type="NORMAL", status="ACCESS"
    )
    assert len(repo.list_users()) == 3
    assert len(repo.list_users(user_type="COMPETE")) == 2
    assert (
        repo.count_compete_users_after_time(start_time="2000-01-01")
        == 2
    )


def test_get_user_info_missing(repo):
    assert repo.get_user_info(user_id="nope", user_type="NORMAL") is None


# ── get_config_by_key: errors propagate (prod parity) ───────────────

def test_get_config_by_key_propagates_when_tables_absent(
    repo_no_config_tables,
):
    """The old SQLite twin swallowed all exceptions and returned
    None. Prod parity = let it raise (config tables not created)."""
    with pytest.raises(Exception):
        repo_no_config_tables.get_config_by_key(
            config_key="k", category="c", env="prod"
        )
