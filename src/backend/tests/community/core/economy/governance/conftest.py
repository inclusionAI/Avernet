"""Shared test fixtures and fakes for economy/governance tests."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base


# ---------------------------------------------------------------------------
# Shared fakes — single definition, all test files import from here.
# ---------------------------------------------------------------------------


@dataclass
class FakeGovernanceConfig:
    """Minimal EconomyGovernanceConfig stand-in for all governance tests.

    Fields match the production EconomyGovernanceConfig after config cleanup
    (8 fields: business policies + per-env resources).
    """

    dry_run: bool = False
    skip_weekends: bool = False
    cooldown_days: int = 14
    auto_silence_close_days: int = 7
    notify_channel: str = "markdown"
    tc_card_id: str = "card_cb190863"
    tc_card_template_id: str = ""


class FakeDB:
    """Minimal DatabasePlugin stand-in with in-memory SQLite session.

    Matches the real DatabasePlugin contract: ``orm_session()`` commits on
    clean context-manager exit and rolls back on exception.
    """

    def __init__(self, session_factory):
        self._sf = session_factory

    def orm_session(self):
        @contextmanager
        def _sess():
            s = self._sf()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        return _sess()


class FakeWhitelistSvc:
    """Minimal GovernanceWhitelistRepository stand-in."""

    def get_whitelist_set(self, **kwargs):
        return set()

    def batch_add(self, entries, created_by, **kwargs):
        return {"inserted": len(entries), "skipped": 0}

    def count_by_type(self, **kwargs):
        return 0


class FakeWhitelistService:
    """Minimal GovernanceWhitelistService stand-in.

    Delegates to :class:`FakeWhitelistSvc` for repo-level calls.
    """

    def __init__(self, whitelist_svc: FakeWhitelistSvc | None = None):
        self._whitelist_svc = whitelist_svc or FakeWhitelistSvc()

    def bulk_whitelist(self, bot_ids, reason, operator):
        return {"whitelisted": len(bot_ids), "cancelled": 0}

    def delete_whitelist_entries(self, body, operator):
        return {
            "dry_run": body.get("dry_run", True),
            "would_delete": 0,
            "deleted": 0,
            "not_found": [],
            "affected_owner_bots": [],
        }

    def count_by_type(self, **kwargs):
        return 0

    def batch_add(self, entries, created_by, **kwargs):
        return {"inserted": len(entries), "skipped": 0}


class FakeCache:
    """In-memory dict-based cache for testing."""

    def __init__(self):
        self._store: dict[str, tuple[str, int]] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key, (None,))[0]

    def set(self, key: str, value: str, ttl: int = 0) -> None:
        self._store[key] = (value, ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class FakeNotifySender:
    """No-op GovernanceNotifySender for testing."""

    def send_markdown(self, user_id: str, title: str, content: str) -> str | None:
        return f"fake-msg-{user_id}"

    def send_tc_card(
        self,
        user_id: str,
        reason: str,
        detail_link: str,
        bot_id: str,
        card_id: str,
        notification_data: dict,
        out_track_id_prefix: str = "dingtalk",
    ) -> str | None:
        return f"fake-card-{user_id}"


@dataclass
class FakeDingTalkConfig:
    """Minimal GovernanceDingTalkConfig stand-in for testing."""

    app_key: str = ""
    app_secret: str = ""
    robot_code: str = ""
    iframe_callback_url: str = ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """In-memory SQLite engine with FK pragmas enabled."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return eng


@pytest.fixture()
def tables(engine):
    """Create all governance tables, drop after test."""
    import agentclaw.community.core.economy.governance.contracts.models  # noqa: F401
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session(engine, tables):
    """Transactional ORM session."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    sess = Session()
    yield sess
    sess.close()