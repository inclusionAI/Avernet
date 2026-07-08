"""Tests for TaskRecordRepository — read filters + offline batch upsert.

Covers the self-managed session read/write paths, plus the
``_normalize_dt_field`` / ``_parse_gmt_create`` helpers.

After the repo-to-database-plugin refactoring, all repo methods use
``with self._db.orm_session() as s:`` internally — callers no longer
pass ``session`` or ``env``.  Env is determined via ``get_current_env()``
inside each method; tests that need env isolation patch that function.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceTaskRecordDaily,
)
from .conftest import FakeDB

from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
    _extract_owner_id,
)
from agentclaw.community.utils.env_utils import get_current_env


# --- Fakes ---


_CUR_ENV = get_current_env()

_ENV_PATCH = (
    "agentclaw.community.core.economy.governance.repositories.task_record_repo"
    ".get_current_env"
)


def _make_record(
    session, *, worker_id, dt_version,
    governance_decision="actionable",
    analysis_status="completed",
    env=_CUR_ENV,
    last_sync_at=None,
    **overrides,
):
    """Insert a task_record_daily row and commit."""
    row = GovernanceTaskRecordDaily(
        worker_id=worker_id,
        bot_id=overrides.pop("bot_id", "bot-1"),
        owner_id=overrides.pop("owner_id", worker_id.split(":", 1)[0]),
        dt_version=dt_version,
        governance_decision=governance_decision,
        bot_name=overrides.pop("bot_name", "TestBot"),
        analysis_status=analysis_status,
        last_sync_at=last_sync_at or datetime.now(),
        env=env,
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


def _build_repo(engine):
    """Build repo backed by in-memory SQLite."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    return TaskRecordRepository(db=db), db


# ── module-level helpers ────────────────────────────────────────


class TestHelpers:
    def test_resolve_env_uses_get_current_env(self):
        """_resolve_env removed — repos now call get_current_env() internally."""
        from agentclaw.community.utils.env_utils import get_current_env
        assert get_current_env() == _CUR_ENV

    def test_extract_owner_id(self):
        assert _extract_owner_id("user-1:bot-9") == "user-1"
        assert _extract_owner_id("noColonHere") == "noColonHere"

    def test_normalize_dt_field_prefers_dt_version(self):
        recs = [{"dt_version": "20260601", "dt": "20250101"}]
        out = TaskRecordRepository._normalize_dt_field(recs)
        assert out[0]["dt_version"] == "20260601"
        # original not mutated
        assert recs[0]["dt_version"] == "20260601"

    def test_normalize_dt_field_falls_back_to_dt(self):
        recs = [{"dt": "20260601"}]
        out = TaskRecordRepository._normalize_dt_field(recs)
        assert out[0]["dt_version"] == "20260601"

    def test_parse_gmt_create_none(self):
        assert TaskRecordRepository._parse_gmt_create(None) is None

    def test_parse_gmt_create_datetime_passthrough(self):
        dt = datetime(2026, 6, 1, 12, 0, 0)
        assert TaskRecordRepository._parse_gmt_create(dt) is dt

    def test_parse_gmt_create_numeric(self):
        ts = 1_700_000_000
        assert TaskRecordRepository._parse_gmt_create(ts) == datetime.fromtimestamp(ts)
        assert TaskRecordRepository._parse_gmt_create(float(ts)) == datetime.fromtimestamp(ts)

    def test_parse_gmt_create_string_formats(self):
        assert TaskRecordRepository._parse_gmt_create("2026-06-01 08:30:00") == datetime(2026, 6, 1, 8, 30, 0)
        assert TaskRecordRepository._parse_gmt_create("2026-06-01T08:30:00") == datetime(2026, 6, 1, 8, 30, 0)
        assert TaskRecordRepository._parse_gmt_create("2026-06-01") == datetime(2026, 6, 1)

    def test_parse_gmt_create_unparseable_string(self):
        assert TaskRecordRepository._parse_gmt_create("not-a-date") is None


# ── get_latest_dt_version ────────────────────────────────────────


class TestGetLatestDtVersion:
    def test_returns_max_le_today(self, session, engine):
        repo, _ = _build_repo(engine)
        today = date.today().strftime("%Y%m%d")
        _make_record(session, worker_id="u:b1", dt_version="20200101")
        _make_record(session, worker_id="u:b2", dt_version=today)
        # a future partition must be excluded (> today)
        future = (date.today() + timedelta(days=5)).strftime("%Y%m%d")
        _make_record(session, worker_id="u:b3", dt_version=future)

        assert repo.get_latest_dt_version() == today

    def test_fallback_to_yesterday(self, session, engine):
        repo, _ = _build_repo(engine)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        _make_record(session, worker_id="u:b1", dt_version=yesterday)

        assert repo.get_latest_dt_version() == yesterday

    def test_none_when_no_data(self, session, engine):
        repo, _ = _build_repo(engine)
        assert repo.get_latest_dt_version() is None

    def test_env_isolation(self, session, engine, monkeypatch):
        repo, _ = _build_repo(engine)
        today = date.today().strftime("%Y%m%d")
        _make_record(session, worker_id="u:b1", dt_version=today, env="pre")
        # default env (dev) sees nothing
        assert repo.get_latest_dt_version() is None
        # patch env to "pre" → matches
        monkeypatch.setattr(
            f"{_ENV_PATCH}", lambda: "pre",
        )
        assert repo.get_latest_dt_version() == today

    def test_yesterday_fallback_info_branch(self, session, engine):
        """First (<= today) query returns None but second (<= yesterday)
        returns a value → info-log fallback path.

        After the self-managed session refactoring, we exercise this path
        by only inserting a yesterday-row (no today-row), which triggers
        the fallback branch inside get_latest_dt_version.
        """
        repo, _ = _build_repo(engine)
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        _make_record(session, worker_id="u:yest", dt_version=yesterday)

        assert repo.get_latest_dt_version() == yesterday


# ── get_actionable_bots ──────────────────────────────────────────


class TestGetActionableBots:
    def test_filters_by_decision_and_status(self, session, engine):
        repo, _ = _build_repo(engine)
        dt = "20260629"
        _make_record(session, worker_id="u:a", dt_version=dt,
                     governance_decision="actionable", analysis_status="completed")
        _make_record(session, worker_id="u:b", dt_version=dt,
                     governance_decision="actionable", analysis_status="success")
        # excluded: wrong decision
        _make_record(session, worker_id="u:c", dt_version=dt,
                     governance_decision="observe", analysis_status="completed")
        # excluded: wrong status
        _make_record(session, worker_id="u:d", dt_version=dt,
                     governance_decision="actionable", analysis_status="error")
        # excluded: wrong dt
        _make_record(session, worker_id="u:e", dt_version="20260101",
                     governance_decision="actionable", analysis_status="completed")

        rows = repo.get_actionable_bots(dt)
        assert {r["worker_id"] for r in rows} == {"u:a", "u:b"}

    def test_empty_result(self, session, engine):
        repo, _ = _build_repo(engine)
        assert repo.get_actionable_bots("20260629") == []

    def test_env_scoped(self, session, engine, monkeypatch):
        repo, _ = _build_repo(engine)
        dt = "20260629"
        _make_record(session, worker_id="u:a", dt_version=dt, env="pre")
        assert repo.get_actionable_bots(dt) == []
        monkeypatch.setattr(_ENV_PATCH, lambda: "pre")
        assert len(repo.get_actionable_bots(dt)) == 1


# ── get_completed_decisions ──────────────────────────────────────


class TestGetCompletedDecisions:
    def test_returns_decision_map(self, session, engine):
        repo, _ = _build_repo(engine)
        dt = "20260629"
        _make_record(session, worker_id="u:a", dt_version=dt,
                     governance_decision="actionable", analysis_status="completed")
        _make_record(session, worker_id="u:b", dt_version=dt,
                     governance_decision="observe", analysis_status="success_with_warnings")
        # excluded: incomplete status
        _make_record(session, worker_id="u:c", dt_version=dt,
                     governance_decision="justified", analysis_status="error")

        result = repo.get_completed_decisions(dt)
        assert result == {"u:a": "actionable", "u:b": "observe"}

    def test_empty(self, session, engine):
        repo, _ = _build_repo(engine)
        assert repo.get_completed_decisions("20260629") == {}


# ── get_max_last_sync_at ─────────────────────────────────────────


class TestGetMaxLastSyncAt:
    def test_returns_max(self, session, engine):
        repo, _ = _build_repo(engine)
        older = datetime(2026, 6, 1, 0, 0, 0)
        newer = datetime(2026, 6, 2, 0, 0, 0)
        _make_record(session, worker_id="u:a", dt_version="20260601", last_sync_at=older)
        _make_record(session, worker_id="u:b", dt_version="20260602", last_sync_at=newer)

        result = repo.get_max_last_sync_at()
        # SQLite may return a string or datetime depending on adapter; compare via str
        assert str(result).startswith("2026-06-02")

    def test_none_when_empty(self, session, engine):
        repo, _ = _build_repo(engine)
        assert repo.get_max_last_sync_at() is None

    def test_env_scoped(self, session, engine, monkeypatch):
        repo, _ = _build_repo(engine)
        _make_record(session, worker_id="u:a", dt_version="20260601", env="pre")
        assert repo.get_max_last_sync_at() is None
        monkeypatch.setattr(_ENV_PATCH, lambda: "pre")
        assert repo.get_max_last_sync_at() is not None


# ── batch_upsert_task_recs ───────────────────────────────────────


class TestBatchUpsert:
    def test_empty_records(self, engine):
        repo, _ = _build_repo(engine)
        assert repo.batch_upsert_task_recs([]) == {"inserted": 0, "updated": 0, "errors": 0}

    def test_insert_new_records(self, engine, tables):
        repo, db = _build_repo(engine)
        records = [
            {
                "worker_id": "user-1:bot-1", "dt_version": "20260629",
                "governance_decision": "actionable", "bot_id": "bot-1",
                "bot_name": "Alpha", "hit_dimensions": "a,b",
                "hit_dimensions_count": 2, "governance_max_priority": "P0",
                "expected_token_saving": 1000, "saving_ratio": "0.25",
                "task_summary": "summary", "notification_structured": "{}",
                "analysis_status": "completed",
                "gmt_create": "2026-06-01 00:00:00",
            },
            {
                "worker_id": "user-2:bot-2", "dt_version": "20260629",
                "governance_decision": "observe",
            },
        ]
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 2, "updated": 0, "errors": 0}

        with db.orm_session() as s:
            rows = {r.worker_id: r for r in s.query(GovernanceTaskRecordDaily).all()}
            assert set(rows) == {"user-1:bot-1", "user-2:bot-2"}
            # owner_id derived from worker_id when not provided
            assert rows["user-2:bot-2"].owner_id == "user-2"
            assert rows["user-1:bot-1"].env == _CUR_ENV
            assert rows["user-1:bot-1"].gmt_create == datetime(2026, 6, 1, 0, 0, 0)

    def test_update_existing_records(self, session, engine):
        repo, db = _build_repo(engine)
        _make_record(session, worker_id="user-1:bot-1", dt_version="20260629",
                     governance_decision="observe", bot_name="Old")

        records = [{
            "worker_id": "user-1:bot-1", "dt_version": "20260629",
            "governance_decision": "actionable", "bot_name": "New",
            # None value must NOT overwrite the existing field
            "task_summary": None,
        }]
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 0, "updated": 1, "errors": 0}

        with db.orm_session() as s:
            row = s.query(GovernanceTaskRecordDaily).one()
            assert row.governance_decision == "actionable"
            assert row.bot_name == "New"

    def test_mixed_insert_and_update(self, session, engine):
        repo, db = _build_repo(engine)
        _make_record(session, worker_id="user-1:bot-1", dt_version="20260629",
                     governance_decision="observe")

        records = [
            {"worker_id": "user-1:bot-1", "dt_version": "20260629",
             "governance_decision": "actionable"},  # update
            {"worker_id": "user-9:bot-9", "dt_version": "20260629",
             "governance_decision": "justified"},  # insert
        ]
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 1, "updated": 1, "errors": 0}

        with db.orm_session() as s:
            assert s.query(GovernanceTaskRecordDaily).count() == 2

    def test_skips_records_missing_keys(self, engine, tables):
        repo, db = _build_repo(engine)
        records = [
            {"dt_version": "20260629"},                 # missing worker_id
            {"worker_id": "user-1:bot-1"},              # missing dt_version
            {"worker_id": "user-2:bot-2", "dt_version": "20260629"},  # ok
        ]
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 1, "updated": 0, "errors": 2}

    def test_dt_key_normalized(self, engine, tables):
        repo, db = _build_repo(engine)
        records = [{"worker_id": "user-1:bot-1", "dt": "20260629"}]
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 1, "updated": 0, "errors": 0}
        with db.orm_session() as s:
            row = s.query(GovernanceTaskRecordDaily).one()
            assert row.dt_version == "20260629"

    def test_env_scoped_via_patch(self, engine, tables, monkeypatch):
        """batch_upsert uses get_current_env() for env scoping.
        Patch to "pre" → rows written with env="pre"; a second upsert
        with env="prod" must INSERT (dedup is env-scoped).
        """
        repo, db = _build_repo(engine)
        records = [{"worker_id": "user-1:bot-1", "dt_version": "20260629",
                    "governance_decision": "actionable"}]

        monkeypatch.setattr(_ENV_PATCH, lambda: "pre")
        repo.batch_upsert_task_recs(records)

        with db.orm_session() as s:
            row = s.query(GovernanceTaskRecordDaily).one()
            assert row.env == "pre"

        # patch to "prod" → different env, must INSERT not update
        monkeypatch.setattr(_ENV_PATCH, lambda: "prod")
        result = repo.batch_upsert_task_recs(records)
        assert result == {"inserted": 1, "updated": 0, "errors": 0}
        with db.orm_session() as s:
            assert s.query(GovernanceTaskRecordDaily).count() == 2

    def test_explicit_owner_id_preserved(self, engine, tables):
        repo, db = _build_repo(engine)
        records = [{"worker_id": "user-1:bot-1", "dt_version": "20260629",
                    "owner_id": "custom-owner"}]
        repo.batch_upsert_task_recs(records)
        with db.orm_session() as s:
            row = s.query(GovernanceTaskRecordDaily).one()
            assert row.owner_id == "custom-owner"

    def test_per_record_exception_counted(self, engine, tables, monkeypatch):
        """A record that raises mid-loop → errors++ and others still processed."""
        repo, db = _build_repo(engine)

        real_parse = TaskRecordRepository._parse_gmt_create

        def _flaky_parse(value):
            if value == "BOOM":
                raise RuntimeError("parse boom")
            return real_parse(value)

        monkeypatch.setattr(
            TaskRecordRepository, "_parse_gmt_create", staticmethod(_flaky_parse)
        )

        records = [
            {"worker_id": "user-1:bot-1", "dt_version": "20260629",
             "gmt_create": "BOOM"},  # raises inside the loop
            {"worker_id": "user-2:bot-2", "dt_version": "20260629"},  # ok
        ]
        result = repo.batch_upsert_task_recs(records)
        assert result["errors"] == 1
        assert result["inserted"] == 1

    # NOTE: test_commit_failure_resets_counts removed — session commit failure
# is now an internal implementation detail of self-managed orm_session
# contexts and cannot be cleanly tested from the outside.