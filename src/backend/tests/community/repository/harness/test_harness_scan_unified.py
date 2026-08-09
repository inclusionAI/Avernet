"""Unified HarnessScanRecord repository — behavior + contract.

Round-3/session-3 criteria: single ORM body, 12-method Protocol
parity. This is harness_scan's first test coverage (neither twin had
one). Covers the prod-parity guarantees: plain INSERT (no upsert),
``offline_batch`` emulated SELECT-then-branch (insert + update + mixed
batch), single blind UPDATE that no-ops silently when the id is
missing, and ``update_patch_ids`` ignoring ``findings_with_patch_ids``.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.harness.models import FindingsReport, Layer
from agentclaw.community.core.repository.implementations.harness.scan import HarnessScanRecordRepository

pytestmark = pytest.mark.integration


def _create_schema(engine):
    from agentclaw.community.core.harness.sqlite_models import HarnessScanRecordModel

    src = HarnessScanRecordModel.__table__
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
        f"sqlite:///{tmp_path / 'hsr.db'}",
        connect_args={"check_same_thread": False},
    )
    _create_schema(engine)
    return _FileSqliteDB(engine)


@pytest.fixture
def repo(tmp_path):
    return HarnessScanRecordRepository(_make_db(tmp_path))


def _report(**overrides):
    base = dict(
        bot_id="bot-1",
        entity_id="ent-1",
        scan_type="full",
        layer=Layer.L1,
        health_score=88,
        score_grade="good",
        trigger_source="api",
        duration_ms=1200,
        status="completed",
        env="dev",
        bot_publish_id=None,
    )
    base.update(overrides)
    return FindingsReport(**base)


def _rec(**overrides):
    base = dict(
        scan_dim="security",
        scan_type="full",
        health_score=70,
        score_grade="good",
        check_items="[]",
        findings="[]",
        findings_summary="{}",
        duration_ms=10,
        status="completed",
        failed_reason=None,
        env="dev",
    )
    base.update(overrides)
    return base


# ── create / get_by_id (plain INSERT) ───────────────────────────────

def test_create_and_get_by_id(repo):
    sid = repo.create(_report())
    assert isinstance(sid, int) and sid > 0
    row = repo.get_by_id(sid)
    assert row["bot_id"] == "bot-1"
    assert row["health_score"] == 88
    assert row["scan_dim"] == "full:L1"


def test_get_by_id_missing_returns_none(repo):
    assert repo.get_by_id(999999) is None


def test_create_is_plain_insert_not_upsert(repo):
    """Two creates with the same logical key produce two rows."""
    a = repo.create(_report())
    b = repo.create(_report())
    assert a != b
    rows, total = repo.list_records("bot-1", "ent-1")
    assert total == 2


# ── batch_create (plain INSERT loop) ────────────────────────────────

def test_batch_create(repo):
    ids = repo.batch_create(
        "bot-1", "ent-1", None, "L1", "api",
        [_rec(scan_dim="a"), _rec(scan_dim="b")],
    )
    assert len(ids) == 2
    rows, total = repo.list_records("bot-1", "ent-1")
    assert total == 2


# ── offline_batch (emulated SELECT-then-branch) ─────────────────────

def test_offline_batch_insert_then_update(repo):
    # First call: all inserts.
    r1 = repo.offline_batch(
        "bot-1", "ent-1", None, "L1", "api",
        [_rec(scan_dim="security", scan_type="full", health_score=50)],
    )
    assert r1[0]["action"] == "inserted"
    first_id = r1[0]["id"]

    # Second call, same logical key: must UPDATE the same row.
    r2 = repo.offline_batch(
        "bot-1", "ent-1", None, "L1", "api",
        [_rec(scan_dim="security", scan_type="full", health_score=95)],
    )
    assert r2[0]["action"] == "updated"
    assert r2[0]["id"] == first_id

    row = repo.get_by_id(first_id)
    assert row["health_score"] == 95
    # No duplicate row created.
    _, total = repo.list_records("bot-1", "ent-1")
    assert total == 1


def test_offline_batch_mixed(repo):
    res = repo.offline_batch(
        "bot-1", "ent-1", None, "L1", "api",
        [
            _rec(scan_dim="security", scan_type="full"),
            _rec(scan_dim="perf", scan_type="full"),
        ],
    )
    assert {r["action"] for r in res} == {"inserted"}
    res2 = repo.offline_batch(
        "bot-1", "ent-1", None, "L1", "api",
        [
            _rec(scan_dim="security", scan_type="full", health_score=10),
            _rec(scan_dim="new-dim", scan_type="full"),
        ],
    )
    actions = {r["scan_dim"]: r["action"] for r in res2}
    assert actions["security"] == "updated"
    assert actions["new-dim"] == "inserted"


def test_offline_batch_entity_isolation(repo):
    repo.offline_batch(
        "bot-1", "ent-1", None, "L1", "api",
        [_rec(scan_dim="security", scan_type="full")],
    )
    r = repo.offline_batch(
        "bot-1", "ent-2", None, "L1", "api",
        [_rec(scan_dim="security", scan_type="full")],
    )
    # Different entity_id → separate row, an insert not an update.
    assert r[0]["action"] == "inserted"


# ── get_latest_dim_records ──────────────────────────────────────────

def test_get_latest_dim_records(repo):
    repo.batch_create(
        "bot-1", "ent-1", None, "L1", "api",
        [
            _rec(scan_dim="security", health_score=10),
            _rec(scan_dim="security", health_score=99),
            _rec(scan_dim="perf", health_score=42),
        ],
    )
    latest = repo.get_latest_dim_records("bot-1", "ent-1")
    by_dim = {r["scan_dim"]: r for r in latest}
    assert set(by_dim) == {"security", "perf"}
    # Latest security row (highest id wins under gmt_create DESC tie →
    # insertion order; the last-inserted security row had score 99).
    assert by_dim["security"]["health_score"] == 99


# ── get_recent / list_records ───────────────────────────────────────

def test_get_recent_filters(repo):
    repo.create(_report(scan_type="full", health_score=1))
    repo.create(_report(scan_type="quick", health_score=2))
    rec = repo.get_recent("bot-1", "ent-1", scan_type="quick")
    assert rec["health_score"] == 2


def test_list_records_pagination(repo):
    for i in range(5):
        repo.create(_report(health_score=i))
    page1, total = repo.list_records("bot-1", "ent-1", page=1, size=2)
    assert total == 5
    assert len(page1) == 2


# ── update family (single blind UPDATE, prod semantics) ─────────────

def test_update_status(repo):
    sid = repo.create(_report(status="scanning"))
    repo.update_status(sid, "completed")
    assert repo.get_by_id(sid)["status"] == "completed"
    repo.update_status(sid, "failed", failed_reason="boom")
    row = repo.get_by_id(sid)
    assert row["status"] == "failed"
    assert row["failed_reason"] == "boom"


def test_update_status_missing_id_is_silent_noop(repo):
    # Prod parity: blind UPDATE affects 0 rows, no exception, no SELECT.
    repo.update_status(123456, "completed")  # must not raise


def test_complete(repo):
    sid = repo.create(_report(status="scanning", health_score=0))
    repo.complete(sid, _report(status="completed", health_score=77))
    row = repo.get_by_id(sid)
    assert row["status"] == "completed"
    assert row["health_score"] == 77


def test_update_findings(repo):
    sid = repo.create(_report())
    repo.update_findings(
        sid, '[{"x":1}]', '{"total":1}', '[{"c":1}]', 55, "warning"
    )
    row = repo.get_by_id(sid)
    assert row["health_score"] == 55
    assert row["score_grade"] == "warning"


def test_update_patch_ids_ignores_findings_arg(repo):
    """Prod parity: only patch_ids is written; findings untouched."""
    sid = repo.create(_report())
    before = repo.get_by_id(sid)["findings"]
    repo.update_patch_ids(
        sid, [1, 2, 3], findings_with_patch_ids='[{"injected":true}]'
    )
    row = repo.get_by_id(sid)
    assert row["patch_ids"] == [1, 2, 3]
    # findings must NOT have been overwritten by the ignored arg.
    assert row["findings"] == before


# ── has_active_scan ─────────────────────────────────────────────────

def test_has_active_scan(repo):
    assert repo.has_active_scan("bot-1", "ent-1") is False
    repo.create(_report(status="scanning"))
    assert repo.has_active_scan("bot-1", "ent-1") is True
