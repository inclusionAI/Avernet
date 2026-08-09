"""Tests for GovernanceWhitelistRepository — add, is_whitelisted, list_by_owner, count, env."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from .conftest import FakeDB

from agentclaw.community.core.economy.governance.orm import WhitelistEntryOrm
from agentclaw.community.core.repository.implementations.governance.whitelist import GovernanceWhitelistRepository


# --- Fakes ---




def _build_repo(engine):
    """Build a repository wired to an in-memory DB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session())
    repo = GovernanceWhitelistRepository(db=db)
    return repo, db


# ── add ────────────────────────────────────────────────────


class TestAdd:
    """GovernanceWhitelistRepository.add() — single-point, idempotent."""

    def test_inserts_new_entry(self, engine, tables):
        repo, db = _build_repo(engine)
        entry = repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", source="admin", reason="spam",
        )
        assert entry.bot_id == "bot-1"
        assert entry.owner_id == "user-1"
        assert entry.source == "admin"
        assert entry.created_by == "admin"
        assert entry.whitelist_type == "governance"
        assert entry.reason == "spam"

        with db.orm_session() as s:
            rows = s.query(WhitelistEntryOrm).all()
            assert len(rows) == 1
            assert rows[0].bot_id == "bot-1"
            assert rows[0].env == "dev"

    def test_idempotent_returns_existing(self, engine, tables):
        repo, db = _build_repo(engine)
        repo.add(
            bot_id="bot-1", owner_id="user-1", created_by="admin",
        )
        second = repo.add(
            bot_id="bot-1", owner_id="user-1", created_by="other",
        )
        # Second call returns the existing entry (not modified)
        assert second.bot_id == "bot-1"
        assert second.created_by == "admin"  # unchanged

        with db.orm_session() as s:
            assert s.query(WhitelistEntryOrm).count() == 1

    def test_default_reason_empty(self, engine, tables):
        repo, db = _build_repo(engine)
        entry = repo.add(bot_id="bot-2", owner_id="user-1", created_by="admin")
        assert entry.reason == ""

    def test_reason_truncated_to_500_chars(self, engine, tables):
        repo, db = _build_repo(engine)
        long_reason = "x" * 600
        entry = repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", reason=long_reason,
        )
        assert len(entry.reason) == 500

        with db.orm_session() as s:
            row = s.query(WhitelistEntryOrm).one()
            assert len(row.reason) == 500

    def test_expires_at_from_datetime(self, engine, tables):
        repo, db = _build_repo(engine)
        future = datetime(2026, 8, 1, 12, 0, 0)
        entry = repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", expires_at=future,
        )
        assert entry.expires_at == future

        with db.orm_session() as s:
            row = s.query(WhitelistEntryOrm).one()
            assert row.expires_at == future

    def test_different_whitelist_type_not_duplicate(self, engine, tables):
        """Different whitelist_type → not a duplicate."""
        repo, db = _build_repo(engine)
        repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", whitelist_type="governance",
        )
        entry2 = repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", whitelist_type="dormant",
        )
        assert entry2.whitelist_type == "dormant"

        with db.orm_session() as s:
            assert s.query(WhitelistEntryOrm).count() == 2

    def test_commit_failure_rolls_back_and_raises(self, engine, tables):
        """Commit exception → rollback + re-raise."""
        repo, _ = _build_repo(engine)

        class _BoomSession:
            def __init__(self, real):
                self._real = real
                self.rolled_back = False

            def query(self, *a, **k):
                return self._real.query(*a, **k)

            def add(self, *a, **k):
                return self._real.add(*a, **k)

            def commit(self):
                raise RuntimeError("boom")

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self._real.close()

        Session = sessionmaker(bind=engine, expire_on_commit=False)
        boom = _BoomSession(Session())

        @contextmanager
        def _sess():
            try:
                yield boom
            finally:
                boom.close()

        class _DB:
            def orm_session(self_inner):
                return _sess()

        repo_boom = GovernanceWhitelistRepository(db=_DB())
        with pytest.raises(RuntimeError, match="boom"):
            repo_boom.add(bot_id="b", owner_id="o", created_by="admin")
        assert boom.rolled_back is True


# ── is_whitelisted ───────────────────────────────────────


class TestIsWhitelisted:
    def test_returns_true_for_active_entry(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        assert repo.is_whitelisted("bot-1", "user-1") is True

    def test_returns_false_for_missing(self, engine, tables):
        repo, _ = _build_repo(engine)
        assert repo.is_whitelisted("bot-x", "user-x") is False

    def test_excludes_expired(self, engine, tables):
        repo, _ = _build_repo(engine)
        past = datetime.now() - timedelta(days=1)
        future = datetime.now() + timedelta(days=1)
        repo.add(
            bot_id="bot-perm", owner_id="user-1",
            created_by="admin",  # no expiry
        )
        repo.add(
            bot_id="bot-future", owner_id="user-1",
            created_by="admin", expires_at=future,
        )
        repo.add(
            bot_id="bot-past", owner_id="user-1",
            created_by="admin", expires_at=past,
        )
        assert repo.is_whitelisted("bot-perm", "user-1") is True
        assert repo.is_whitelisted("bot-future", "user-1") is True
        assert repo.is_whitelisted("bot-past", "user-1") is False


# ── count_by_type ────────────────────────────────────────


class TestCountByType:
    def test_count_by_type(self, engine, tables, session):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(bot_id="bot-2", owner_id="user-1", created_by="admin")
        repo.add(
            bot_id="bot-3", owner_id="user-2",
            created_by="admin", whitelist_type="dormant",
        )
        assert repo.count_by_type(whitelist_type="governance") == 2
        assert repo.count_by_type(whitelist_type="dormant") == 1
        assert repo.count_by_type(whitelist_type="none") == 0


# ── list_by_owner ────────────────────────────────────────


class TestListByOwner:
    def test_returns_domain_models(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(
            bot_id="bot-1", owner_id="user-1",
            created_by="admin", reason="r1",
            expires_at=datetime(2026, 9, 1, 0, 0),
        )
        rows = repo.list_by_owner("user-1")
        assert len(rows) == 1
        entry = rows[0]
        assert entry.bot_id == "bot-1"
        assert entry.owner_id == "user-1"
        assert entry.reason == "r1"
        assert entry.whitelist_type == "governance"
        assert entry.created_by == "admin"
        assert entry.expires_at == datetime(2026, 9, 1, 0, 0)

    def test_filters_by_owner(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(bot_id="bot-2", owner_id="user-2", created_by="admin")
        rows = repo.list_by_owner("user-1")
        assert len(rows) == 1
        assert rows[0].owner_id == "user-1"

    def test_respects_limit_and_offset(self, engine, tables):
        repo, _ = _build_repo(engine)
        for i in range(5):
            repo.add(bot_id=f"bot-{i}", owner_id="user-1", created_by="admin")
        page = repo.list_by_owner("user-1", limit=2, offset=0)
        assert len(page) == 2
        page2 = repo.list_by_owner("user-1", limit=2, offset=2)
        assert len(page2) == 2

    def test_empty_expires_null(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        rows = repo.list_by_owner("user-1")
        assert rows[0].expires_at is None


# ── list_all ─────────────────────────────────────────────


class TestListAll:
    """GovernanceWhitelistRepository.list_all() — 全量分页 + 筛选 + 过期开关 + total."""

    def test_returns_domain_models_with_total(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin", reason="r1")
        repo.add(bot_id="bot-2", owner_id="user-2", created_by="admin")
        rows, total = repo.list_all()
        assert total == 2
        assert len(rows) == 2
        assert all(r.whitelist_type == "governance" for r in rows)
        # to_dict 序列化含新开的时间元信息字段(Task 1)
        d = rows[0].to_dict()
        assert "gmt_create" in d and "gmt_modified" in d

    def test_filters_by_whitelist_type(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(
            bot_id="bot-2", owner_id="user-1",
            created_by="admin", whitelist_type="dormant",
        )
        rows_gov, total_gov = repo.list_all(whitelist_type="governance")
        assert total_gov == 1
        assert rows_gov[0].whitelist_type == "governance"
        rows_dor, total_dor = repo.list_all(whitelist_type="dormant")
        assert total_dor == 1
        assert rows_dor[0].whitelist_type == "dormant"

    def test_filters_by_owner(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(bot_id="bot-2", owner_id="user-2", created_by="admin")
        rows, total = repo.list_all(owner_id="user-1")
        assert total == 1
        assert all(r.owner_id == "user-1" for r in rows)

    def test_filters_by_bot(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(bot_id="bot-2", owner_id="user-1", created_by="admin")
        rows, total = repo.list_all(bot_id="bot-1")
        assert total == 1
        assert all(r.bot_id == "bot-1" for r in rows)

    def test_default_excludes_expired(self, engine, tables):
        repo, _ = _build_repo(engine)
        past = datetime.now() - timedelta(days=1)
        future = datetime.now() + timedelta(days=1)
        repo.add(bot_id="bot-perm", owner_id="u", created_by="admin")
        repo.add(bot_id="bot-future", owner_id="u", created_by="admin", expires_at=future)
        repo.add(bot_id="bot-past", owner_id="u", created_by="admin", expires_at=past)
        rows, total = repo.list_all()  # include_expired 默认 False
        bot_ids = {r.bot_id for r in rows}
        assert total == 2
        assert "bot-perm" in bot_ids
        assert "bot-future" in bot_ids
        assert "bot-past" not in bot_ids

    def test_include_expired_returns_all(self, engine, tables):
        repo, _ = _build_repo(engine)
        past = datetime.now() - timedelta(days=1)
        repo.add(bot_id="bot-perm", owner_id="u", created_by="admin")
        repo.add(bot_id="bot-past", owner_id="u", created_by="admin", expires_at=past)
        rows, total = repo.list_all(include_expired=True)
        assert total == 2
        assert {r.bot_id for r in rows} == {"bot-perm", "bot-past"}

    def test_pagination(self, engine, tables):
        repo, _ = _build_repo(engine)
        for i in range(5):
            repo.add(bot_id=f"bot-{i}", owner_id="user-1", created_by="admin")
        page1, total = repo.list_all(limit=2, offset=0)
        page2, _ = repo.list_all(limit=2, offset=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        # 按 gmt_create 倒序:且两页不重叠
        assert {r.bot_id for r in page1}.isdisjoint({r.bot_id for r in page2})

    def test_empty_db_returns_zero(self, engine, tables):
        repo, _ = _build_repo(engine)
        rows, total = repo.list_all()
        assert rows == []
        assert total == 0

    def test_combined_filters(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        repo.add(bot_id="bot-1", owner_id="user-2", created_by="admin")
        repo.add(bot_id="bot-2", owner_id="user-1", created_by="admin")
        rows, total = repo.list_all(owner_id="user-1", bot_id="bot-1")
        assert total == 1
        assert rows[0].owner_id == "user-1"
        assert rows[0].bot_id == "bot-1"


# ── remove ────────────────────────────────────────────────


class TestRemove:
    def test_remove_existing(self, engine, tables):
        repo, db = _build_repo(engine)
        repo.add(bot_id="bot-1", owner_id="user-1", created_by="admin")
        assert repo.remove(bot_id="bot-1", owner_id="user-1") is True

        with db.orm_session() as s:
            assert s.query(WhitelistEntryOrm).count() == 0

    def test_remove_nonexistent(self, engine, tables):
        repo, _ = _build_repo(engine)
        assert repo.remove(bot_id="bot-x", owner_id="user-x") is False


# ── _parse_expires_at ───────────────────────────────────────


class TestParseExpiresAt:
    def test_none(self):
        assert GovernanceWhitelistRepository._parse_expires_at(None) is None

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 1)
        assert GovernanceWhitelistRepository._parse_expires_at(dt) is dt

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-08-01 12:00:00", datetime(2026, 8, 1, 12, 0, 0)),
            ("2026-08-01T12:00:00", datetime(2026, 8, 1, 12, 0, 0)),
            ("2026-08-01", datetime(2026, 8, 1)),
        ],
    )
    def test_string_formats(self, raw, expected):
        assert GovernanceWhitelistRepository._parse_expires_at(raw) == expected

    def test_unparseable_string_returns_none(self):
        assert GovernanceWhitelistRepository._parse_expires_at("not-a-date") is None

    def test_unsupported_type_returns_none(self):
        assert GovernanceWhitelistRepository._parse_expires_at(12345) is None