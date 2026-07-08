"""Tests for GovernanceWhitelistRepository — batch_add, count, list, lookup, env."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from .conftest import FakeDB

from agentclaw.community.core.economy.governance.contracts.models import BotWhitelist
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)


# --- Fakes ---




def _build_repo(engine):
    """Build a repository wired to an in-memory DB."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session())
    repo = GovernanceWhitelistRepository(db=db)
    return repo, db


# ── batch_add ───────────────────────────────────────────────────


class TestBatchAdd:
    """GovernanceWhitelistRepository.batch_add()."""

    def test_empty_input_short_circuits(self, engine, tables):
        repo, _ = _build_repo(engine)
        result = repo.batch_add([], created_by="admin")
        assert result == {"inserted": 0, "skipped": 0}

    def test_inserts_new_entries(self, engine, tables):
        repo, db = _build_repo(engine)
        entries = [
            {"bot_id": "bot-1", "owner_id": "user-1", "reason": "spam"},
            {"bot_id": "bot-2", "owner_id": "user-1"},
        ]
        result = repo.batch_add(entries, created_by="admin", source="admin")
        assert result == {"inserted": 2, "skipped": 0}

        with db.orm_session() as s:
            rows = s.query(BotWhitelist).all()
            assert len(rows) == 2
            by_bot = {r.bot_id: r for r in rows}
            assert by_bot["bot-1"].owner_id == "user-1"
            assert by_bot["bot-1"].reason == "spam"
            assert by_bot["bot-1"].source == "admin"
            assert by_bot["bot-1"].created_by == "admin"
            assert by_bot["bot-1"].whitelist_type == "governance"
            assert by_bot["bot-1"].env == "dev"
            # Missing optional reason defaults to ""
            assert by_bot["bot-2"].reason == ""

    def test_skips_entries_missing_bot_id_or_owner_id(self, engine, tables):
        repo, db = _build_repo(engine)
        entries = [
            {"owner_id": "user-1"},            # missing bot_id
            {"bot_id": "bot-x"},               # missing owner_id
            {"bot_id": "", "owner_id": "u"},   # falsy bot_id
            {"bot_id": "bot-ok", "owner_id": "user-ok"},
        ]
        result = repo.batch_add(entries, created_by="admin")
        assert result == {"inserted": 1, "skipped": 3}

        with db.orm_session() as s:
            rows = s.query(BotWhitelist).all()
            assert len(rows) == 1
            assert rows[0].bot_id == "bot-ok"

    def test_skips_existing_duplicate(self, engine, tables):
        repo, db = _build_repo(engine)
        repo.batch_add(
            [{"bot_id": "bot-1", "owner_id": "user-1"}], created_by="admin"
        )
        # Re-adding the same (bot_id, owner_id, type, env) → skipped.
        result = repo.batch_add(
            [
                {"bot_id": "bot-1", "owner_id": "user-1"},
                {"bot_id": "bot-2", "owner_id": "user-1"},
            ],
            created_by="admin",
        )
        assert result == {"inserted": 1, "skipped": 1}

        with db.orm_session() as s:
            assert s.query(BotWhitelist).count() == 2

    def test_reason_truncated_to_500_chars(self, engine, tables):
        repo, db = _build_repo(engine)
        long_reason = "x" * 600
        repo.batch_add(
            [{"bot_id": "bot-1", "owner_id": "user-1", "reason": long_reason}],
            created_by="admin",
        )
        with db.orm_session() as s:
            row = s.query(BotWhitelist).one()
            assert len(row.reason) == 500

    def test_expires_at_parsed_from_string(self, engine, tables):
        repo, db = _build_repo(engine)
        repo.batch_add(
            [
                {
                    "bot_id": "bot-1",
                    "owner_id": "user-1",
                    "expires_at": "2026-08-01 12:00:00",
                }
            ],
            created_by="admin",
        )
        with db.orm_session() as s:
            row = s.query(BotWhitelist).one()
            assert row.expires_at == datetime(2026, 8, 1, 12, 0, 0)

    def test_env_isolation_allows_same_pair_in_other_type(self, engine, tables):
        """Different whitelist_type → not a duplicate."""
        repo, db = _build_repo(engine)
        repo.batch_add(
            [{"bot_id": "bot-1", "owner_id": "user-1"}],
            created_by="admin",
            whitelist_type="governance",
        )
        result = repo.batch_add(
            [{"bot_id": "bot-1", "owner_id": "user-1"}],
            created_by="admin",
            whitelist_type="dormant",
        )
        assert result == {"inserted": 1, "skipped": 0}
        with db.orm_session() as s:
            assert s.query(BotWhitelist).count() == 2

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
            repo_boom.batch_add(
                [{"bot_id": "b", "owner_id": "o"}], created_by="admin"
            )
        assert boom.rolled_back is True


# ── count_by_type / get_whitelist_set ───────────────────────────


class TestCountAndSet:
    def test_count_by_type(self, engine, tables, session):
        repo, _ = _build_repo(engine)
        repo.batch_add(
            [
                {"bot_id": "bot-1", "owner_id": "user-1"},
                {"bot_id": "bot-2", "owner_id": "user-1"},
            ],
            created_by="admin",
        )
        repo.batch_add(
            [{"bot_id": "bot-3", "owner_id": "user-2"}],
            created_by="admin",
            whitelist_type="dormant",
        )
        assert repo.count_by_type(whitelist_type="governance") == 2
        assert repo.count_by_type(whitelist_type="dormant") == 1
        assert repo.count_by_type(whitelist_type="none") == 0

    def test_get_whitelist_set_excludes_expired(self, engine, tables, session):
        repo, _ = _build_repo(engine)
        past = datetime.now() - timedelta(days=1)
        future = datetime.now() + timedelta(days=1)
        repo.batch_add(
            [
                {"bot_id": "bot-perm", "owner_id": "user-1"},  # no expiry
                {"bot_id": "bot-future", "owner_id": "user-1", "expires_at": future},
                {"bot_id": "bot-past", "owner_id": "user-1", "expires_at": past},
            ],
            created_by="admin",
        )
        result = repo.get_whitelist_set()
        assert ("bot-perm", "user-1") in result
        assert ("bot-future", "user-1") in result
        assert ("bot-past", "user-1") not in result


# ── list_all ────────────────────────────────────────────────────


class TestListAll:
    def test_list_all_returns_dicts(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.batch_add(
            [
                {
                    "bot_id": "bot-1",
                    "owner_id": "user-1",
                    "reason": "r1",
                    "expires_at": "2026-09-01 00:00:00",
                }
            ],
            created_by="admin",
        )
        rows = repo.list_all()
        assert len(rows) == 1
        entry = rows[0]
        assert entry["bot_id"] == "bot-1"
        assert entry["owner_id"] == "user-1"
        assert entry["reason"] == "r1"
        assert entry["whitelist_type"] == "governance"
        assert entry["created_by"] == "admin"
        assert entry["expires_at"] == datetime(2026, 9, 1, 0, 0)
        assert entry["gmt_create"] is not None
        assert entry["id"] is not None

    def test_list_all_filters_by_owner(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.batch_add(
            [
                {"bot_id": "bot-1", "owner_id": "user-1"},
                {"bot_id": "bot-2", "owner_id": "user-2"},
            ],
            created_by="admin",
        )
        rows = repo.list_all(owner_id="user-1")
        assert len(rows) == 1
        assert rows[0]["owner_id"] == "user-1"

    def test_list_all_respects_limit_and_offset(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.batch_add(
            [{"bot_id": f"bot-{i}", "owner_id": "user-1"} for i in range(5)],
            created_by="admin",
        )
        page = repo.list_all(limit=2, offset=0)
        assert len(page) == 2
        page2 = repo.list_all(limit=2, offset=2)
        assert len(page2) == 2
        # Non-overlapping ids across pages.
        assert {r["id"] for r in page}.isdisjoint({r["id"] for r in page2})

    def test_list_all_empty_expires_null(self, engine, tables):
        repo, _ = _build_repo(engine)
        repo.batch_add(
            [{"bot_id": "bot-1", "owner_id": "user-1"}], created_by="admin"
        )
        rows = repo.list_all()
        assert rows[0]["expires_at"] is None


# ── _parse_expires_at ───────────────────────────────────────────


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
