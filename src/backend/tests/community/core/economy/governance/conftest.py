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
    tc_card_preview_url: str = "https://teamclaw.alipay.com/preview"
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

    def is_whitelisted(self, bot_id, owner_id, **kwargs):
        return False

    def add(self, *, bot_id, owner_id, created_by, whitelist_type="governance",
            source="manual", reason="", expires_at=None):
        from agentclaw.community.core.economy.governance.domain.domain import WhitelistEntry
        return WhitelistEntry(
            bot_id=bot_id, owner_id=owner_id,
            whitelist_type=whitelist_type, source=source,
            reason=reason, created_by=created_by, expires_at=expires_at,
        )

    def remove(self, *, bot_id, owner_id, whitelist_type="governance"):
        return True

    def list_by_owner(self, owner_id, *, whitelist_type="governance",
                      limit=100, offset=0):
        return []

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

    def delete_whitelist_entry(self, *, bot_id, owner_id, reason, operator):
        return {"deleted": True, "bot_id": bot_id, "owner_id": owner_id}

    def add(self, *, bot_id, owner_id, created_by, whitelist_type="governance",
            source="manual", reason=""):
        return self._whitelist_svc.add(
            bot_id=bot_id, owner_id=owner_id, created_by=created_by,
            whitelist_type=whitelist_type, source=source, reason=reason,
        )

    def list_by_owner(self, owner_id, *, whitelist_type="governance",
                      limit=100, offset=0):
        return self._whitelist_svc.list_by_owner(
            owner_id, whitelist_type=whitelist_type, limit=limit, offset=offset,
        )

    def is_whitelisted(self, bot_id, owner_id, *, whitelist_type="governance"):
        return self._whitelist_svc.is_whitelisted(
            bot_id, owner_id, whitelist_type=whitelist_type,
        )

    def count_by_type(self, **kwargs):
        return 0


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
    """Fake NotifySenderPlugin for unit testing.

    Implements the ``NotifySenderPlugin`` Protocol surface (``send`` + ``channels``).
    Returns deterministic fake IDs so tests can assert send outcomes.
    """

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"markdown", "tc_card"})

    def send(self, message: object, *, channel: str = "markdown") -> str | None:
        if channel == "tc_card":
            return f"fake-card-{message.recipient}"
        return f"fake-msg-{message.recipient}"


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
    import agentclaw.community.core.economy.governance.repositories.orm  # noqa: F401
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