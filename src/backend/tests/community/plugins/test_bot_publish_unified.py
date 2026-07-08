"""Unified BotPublish repository — behavior + contract.

Round-3/session-4 criteria: single ORM body, 13-method
``BotPublishRepositoryProtocol`` parity. Covers the prod-parity
guarantees: plain INSERT (no upsert despite uk_oi_p_b_v), single
optimistic-lock UPDATEs (wrong source-status → None, no row touched),
and single hard DELETE.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.plugins.bot_publish_repository import (
    BotPublishRepository,
)

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


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bp.db'}",
        connect_args={"check_same_thread": False},
    )
    BotPublishModel.__table__.create(engine)
    return BotPublishRepository(_FileSqliteDB(engine))


def _data(**overrides):
    base = dict(
        source_bot_pk=1,
        source_bot_id="src-bot",
        publish_bot_id="src-bot.pub.1",
        name="My Bot",
        description="desc",
        owner_id="emp001",
        owner_name="Alice",
        status="DRAFT",
        version=1,
        last_pub_id=0,
        env="dev",
        ext={"k": "v"},
        permission_owner="emp001",
    )
    base.update(overrides)
    return base


# ── insert (plain INSERT — no upsert) ───────────────────────────────

def test_insert_and_get_by_id(repo):
    rec = repo.insert(_data())
    assert rec.id > 0
    assert rec.name == "My Bot"
    assert rec.ext == {"k": "v"}
    got = repo.get_by_id(rec.id)
    assert got.id == rec.id


def test_insert_is_plain_not_upsert(repo):
    """Same (owner_id, publish_bot_id, version) inserts a 2nd row,
    not an upsert (prod parity — versioned/append-only)."""
    a = repo.insert(_data())
    b = repo.insert(_data())
    assert a.id != b.id
    rows = repo.list_by_owner("emp001", "dev")
    assert len(rows) == 2


def test_get_by_id_missing(repo):
    assert repo.get_by_id(999999) is None


# ── queries ─────────────────────────────────────────────────────────

def test_query_methods(repo):
    r = repo.insert(_data(status="RELEASE", version=2, last_pub_id=42))
    assert repo.get_by_publish_bot_id(
        "src-bot.pub.1", "emp001", "dev"
    ).id == r.id
    assert repo.get_by_publish_bot_id(
        "src-bot.pub.1", "emp001", "dev", publish_status="RELEASE"
    ).id == r.id
    assert repo.get_by_publish_bot_id_and_version(
        "src-bot.pub.1", "emp001", 2, "dev"
    ).id == r.id
    assert [x.id for x in repo.list_by_source_bot(1, "dev")] == [r.id]
    assert [x.id for x in repo.list_by_status("RELEASE", "dev")] == [
        r.id
    ]
    assert repo.get_by_last_pub_id(42).id == r.id


def test_query_env_isolation(repo):
    repo.insert(_data(env="dev"))
    repo.insert(_data(env="pre"))
    assert len(repo.list_by_owner("emp001", "dev")) == 1
    assert len(repo.list_by_owner("emp001", "pre")) == 1


# ── draft lookup (owner-agnostic, for teclaw artifact recording) ─────

_DRAFT = PublishStatus.DRAFT  # "draft" — the stored enum value


def test_get_draft_by_publish_bot_id_returns_latest_draft(repo):
    # Two DRAFT versions for the same publish_bot_id → highest version wins.
    repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, version=1))
    hi = repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, version=2))
    got = repo.get_draft_by_publish_bot_id("t-bot", "dev")
    assert got is not None and got.id == hi.id


def test_get_draft_by_publish_bot_id_is_owner_agnostic(repo):
    # No owner filter: a draft created under a different owner is still found.
    r = repo.insert(
        _data(publish_bot_id="t-bot", owner_id="creator", status=_DRAFT)
    )
    assert repo.get_draft_by_publish_bot_id("t-bot", "dev").id == r.id


def test_get_draft_by_publish_bot_id_skips_non_draft_and_other_env(repo):
    # Non-DRAFT status and other env are both excluded → None.
    repo.insert(
        _data(publish_bot_id="t-bot", status=PublishStatus.RELEASED, version=3)
    )
    repo.insert(_data(publish_bot_id="t-bot", status=_DRAFT, env="pre"))
    assert repo.get_draft_by_publish_bot_id("t-bot", "dev") is None
    # No row at all → None.
    assert repo.get_draft_by_publish_bot_id("missing", "dev") is None


# ── optimistic-lock updates ─────────────────────────────────────────

def test_update_status_optimistic_match(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status(r.id, "RELEASE", source_status="DRAFT")
    assert out is not None
    assert out.status == "RELEASE"


def test_update_status_optimistic_mismatch_returns_none(repo):
    r = repo.insert(_data(status="DRAFT"))
    # source_status doesn't match current → 0 rows → None, no change.
    out = repo.update_status(r.id, "RELEASE", source_status="RELEASE")
    assert out is None
    assert repo.get_by_id(r.id).status == "DRAFT"


def test_update_status_no_guard(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status(r.id, "FAIL")
    assert out.status == "FAIL"


def test_update_status_with_ext(repo):
    r = repo.insert(_data(status="DRAFT"))
    out = repo.update_status_with_ext(
        r.id, "RELEASE", {"trace": "abc"}, source_status="DRAFT"
    )
    assert out.status == "RELEASE"
    assert out.ext == {"trace": "abc"}
    # mismatch path
    assert repo.update_status_with_ext(
        r.id, "X", {"y": 1}, source_status="DRAFT"
    ) is None


def test_update_version_and_last_pub_id(repo):
    r = repo.insert(_data(version=1))
    out = repo.update_version(r.id, 5, status="RELEASE")
    assert out.version == 5 and out.status == "RELEASE"
    out2 = repo.update_last_pub_id(r.id, 777)
    assert out2.last_pub_id == 777


# ── delete (single hard DELETE) ─────────────────────────────────────

def test_delete_is_hard(repo):
    r = repo.insert(_data())
    assert repo.delete(r.id) is True
    assert repo.get_by_id(r.id) is None
    assert repo.delete(r.id) is False



def test_get_latest_by_source_bot_id_and_owner_and_status_returns_latest_id(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev"))
    latest = repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev", version=2))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="FAILED", env="dev", version=3))
    repo.insert(_data(source_bot_id="src-1", owner_id="other", status="SUCCESS", env="dev", version=4))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="pre", version=5))

    got = repo.get_latest_by_source_bot_id_and_owner_and_status(
        source_bot_id="src-1",
        owner_id="emp001",
        status="SUCCESS",
        env="dev",
    )

    assert got is not None
    assert got.id == latest.id


def test_get_latest_by_source_bot_id_and_owner_and_status_returns_none_when_missing(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="SUCCESS", env="dev"))

    got = repo.get_latest_by_source_bot_id_and_owner_and_status(
        source_bot_id="src-missing",
        owner_id="emp001",
        status="SUCCESS",
        env="dev",
    )

    assert got is None


def test_get_latest_success_by_source_bot_id_owner_agnostic_returns_latest(repo):
    """Multi-instance bot_id → binding_id resolution: latest success row,
    owner-agnostic (org bot entity_id may differ from create owner_id)."""
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="success", env="dev"))
    latest = repo.insert(
        _data(source_bot_id="src-1", owner_id="other-owner", status="success", env="dev", version=2)
    )
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="failed", env="dev", version=3))
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="success", env="pre", version=4))

    got = repo.get_latest_success_by_source_bot_id("src-1", "dev")

    assert got is not None
    assert got.id == latest.id


def test_get_latest_success_by_source_bot_id_returns_none_when_no_success(repo):
    repo.insert(_data(source_bot_id="src-1", owner_id="emp001", status="failed", env="dev"))

    assert repo.get_latest_success_by_source_bot_id("src-1", "dev") is None
    assert repo.get_latest_success_by_source_bot_id("src-missing", "dev") is None
