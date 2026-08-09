"""Tests for DB log source and field mappings."""

import json
import pytest
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_chat.models import AwLangfuseTrace
from agentclaw.community.core.repository.implementations.chat.db import BotChatDbRepository
from agentclaw.community.core.bot_chat.service import _extract_user_input
from agentclaw.community.core.bot_chat.schemas import ConversationObservation, ConversationSession, SessionMetadata
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.plugin_api.models import Base, BotModel


class MockDb:
    """Mock DatabasePlugin for testing."""

    def __init__(self, session_mock):
        self._session = session_mock

    def orm_session(self):
        class Context:
            def __enter__(_self):
                return self._session
            def __exit__(_self, *args):
                pass
        return Context()


class MockSession:
    """Mock SQLAlchemy session."""

    def __init__(self, query_result=None):
        self._query_result = query_result or []
        self._scalar_result = 0

    def query(self, model):
        return MockQuery(self._query_result, self._scalar_result)

    def execute(self, stmt):
        return MockResult(1)


class MockQuery:
    """Mock SQLAlchemy query."""

    def __init__(self, result, scalar_result=0):
        self._result = result
        self._scalar = scalar_result
        self._filters = []

    def filter(self, *conditions):
        self._filters.extend(conditions)
        return self

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def offset(self, n):
        return self

    def limit(self, n):
        return self

    def first(self):
        return self._result[0] if self._result else None

    def all(self):
        return self._result

    def count(self):
        return self._scalar


class MockResult:
    """Mock SQLAlchemy result."""

    def __init__(self, scalar):
        self._scalar = scalar

    def scalar(self):
        return self._scalar


class MockTraceRow:
    """Mock trace row for testing."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 1)
        self.trace_id = kwargs.get("trace_id", "trace-123")
        self.gmt_trace = kwargs.get("gmt_trace", 1780288428373)
        self.name = kwargs.get("name", "Test Session")
        self.input = kwargs.get("input", '[{"role": "user", "content": "hello"}]')
        self.output = kwargs.get("output", None)
        self.session_id = kwargs.get("session_id", "sess-123")
        self.user_id = kwargs.get("user_id", "user1")
        self.trace_metadata = kwargs.get("trace_metadata", '{"attributes": {"key": "value"}}')
        self.latency = kwargs.get("latency", 5.736)
        self.total_cost = kwargs.get("total_cost", 0.010322)
        self.observations = kwargs.get("observations", '["obs1"]')
        self.bot_id = kwargs.get("bot_id", "default")
        self.device_id = kwargs.get("device_id", "dev1")
        self.real_session_id = kwargs.get("real_session_id", "real-sess-123")


class MockAcBotRow:
    """Mock ac_bots row for testing."""

    def __init__(self, bot_id, entity_id):
        self.bot_id = bot_id
        self.entity_id = entity_id


class SqliteTestDb:
    """Small DatabasePlugin-compatible wrapper backed by in-memory SQLite."""

    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self._next_id = 1

    def next_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    @contextmanager
    def orm_session(self):
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _insert_trace(db: SqliteTestDb, **kwargs):
    defaults = {
        "id": db.next_id(),
        "trace_id": "trace-1",
        "gmt_trace": 1780288428373,
        "name": "Test Session",
        "input": '[{"role": "user", "content": "hello"}]',
        "output": '[{"role": "assistant", "content": "world"}]',
        "session_id": "session-key-1",
        "user_id": "user1",
        "trace_metadata": '{"attributes": {"identity.bot_id": "default"}}',
        "latency": 5.736,
        "total_cost": 0.010322,
        "observations": '["obs1"]',
        "bot_id": "default",
        "device_id": "device-1",
        "real_session_id": "real-session-1",
    }
    defaults.update(kwargs)
    with db.orm_session() as session:
        session.add(AwLangfuseTrace(**defaults))


def _insert_bot(db: SqliteTestDb, **kwargs):
    defaults = {
        "bot_id": "bot-a",
        "entity_id": "user1",
        "entity_type": "staff",
        "creator_id": "user1",
        "owner_id": "user1",
    }
    defaults["id"] = db.next_id()
    defaults.update(kwargs)
    with db.orm_session() as session:
        session.add(BotModel(**defaults))


def _insert_bot_collaborator(db: SqliteTestDb, **kwargs):
    defaults = {
        "bot_pk": 1,
        "bot_id": "bot-a",
        "owner_id": "owner1",
        "user_id": "collaborator1",
        "user_name": "Collaborator",
        "role": "admin",
        "operator_id": "owner1",
        "env": "dev",
    }
    defaults.update(kwargs)
    with db.orm_session() as session:
        session.add(BotCollaboratorModel(**defaults))


class TestFieldMappingSemantics:
    """Test critical field mapping semantics per spec."""

    def test_session_key_maps_to_db_session_id(self):
        """API session_key maps to DB session_id column."""
        row = MockTraceRow(
            session_id="agent:main:sess:123",  # API's session_key
            real_session_id="uuid-456",  # API's session_id
        )
        assert row.session_id == "agent:main:sess:123"
        assert row.real_session_id == "uuid-456"

    def test_session_id_maps_to_db_real_session_id(self):
        """API session_id maps to DB real_session_id column."""
        row = MockTraceRow(
            session_id="agent:main:sess:123",
            real_session_id="uuid-456",  # gen_ai.session.id
        )
        assert row.real_session_id == "uuid-456"

    def test_gmt_trace_conversion(self):
        """Test gmt_trace (ms) to ISO 8601 conversion."""
        # 1780288428373 ms = 2026-06-01T04:33:48.373Z
        repo = BotChatDbRepository(MockDb(MockSession()))
        row = MockTraceRow(gmt_trace=1780288428373)

        session = repo._row_to_session(row)
        assert session.timestamp.endswith("Z")
        assert "2026-06-01" in session.timestamp


class TestBotChatDbRepository:
    """Test BotChatDbRepository."""

    def test_row_to_session_basic(self):
        """Test basic row to session mapping."""
        repo = BotChatDbRepository(MockDb(MockSession()))
        row = MockTraceRow(
            trace_id="abc123",
            name="Test Session",
            input='[{"role": "user", "content": "hello"}]',
            user_id="user1",
            latency=5.736,
            total_cost=0.010322,
        )

        session = repo._row_to_session(row)

        assert session.id == "abc123"
        assert session.name == "Test Session"
        assert session.input == "hello"
        assert session.status == "SUCCESS"
        assert session.user_id == "user1"
        assert session.total_cost == 0.010322
        assert session.latency_ms == 5.736

    def test_row_to_session_empty_input(self):
        """Test row with empty input."""
        repo = BotChatDbRepository(MockDb(MockSession()))
        row = MockTraceRow(input=None)

        session = repo._row_to_session(row)
        assert session.input is None

    def test_row_to_session_default_name(self):
        """Test row with empty name defaults to '未命名会话'."""
        repo = BotChatDbRepository(MockDb(MockSession()))
        row = MockTraceRow(name="")

        session = repo._row_to_session(row)
        assert session.name == "未命名会话"

    def test_safe_json_loads(self):
        """Test safe JSON parsing."""
        repo = BotChatDbRepository(MockDb(MockSession()))

        assert repo._safe_json_loads(None, {}) == {}
        assert repo._safe_json_loads("", {}) == {}
        assert repo._safe_json_loads('{"key": "value"}', {}) == {"key": "value"}
        assert repo._safe_json_loads("invalid json", {}) == {}

    def test_get_trace_returns_detached_copy(self):
        """get_trace should not return the session-bound ORM object."""
        row = MockTraceRow(trace_id="trace-detached", bot_id="default")
        repo = BotChatDbRepository(MockDb(MockSession(query_result=[row])))

        result = repo.get_trace("trace-detached")

        assert result is not row
        assert result.trace_id == "trace-detached"
        assert result.bot_id == "default"
        assert result.input == row.input

    def test_row_to_detail_preserves_json_payloads_and_observations(self):
        """Detail mapping should preserve input/output JSON structures."""
        repo = BotChatDbRepository(MockDb(MockSession()))
        row = MockTraceRow(
            trace_id="detail-1",
            input='[{"role": "user", "content": "hello"}]',
            output='[{"role": "assistant", "content": "world"}]',
            trace_metadata='{"attributes": {"identity.owner_id": "user1"}}',
        )
        observations = [ConversationObservation(id="obs-1", type="AGENT")]

        detail = repo._row_to_detail(row, observations=observations)

        assert detail.id == "detail-1"
        assert detail.input == [{"role": "user", "content": "hello"}]
        assert detail.output == [{"role": "assistant", "content": "world"}]
        assert detail.metadata is not None
        assert detail.metadata.attributes["identity.owner_id"] == "user1"
        assert detail.observations == observations

    def test_get_trace_from_sqlite_returns_detached_copy_after_session_close(self):
        """Real ORM query should return a detached copy safe to use after close."""
        db = SqliteTestDb()
        _insert_trace(db, trace_id="trace-real", bot_id="default")
        repo = BotChatDbRepository(db)

        result = repo.get_trace("trace-real")

        assert result is not None
        assert result.trace_id == "trace-real"
        assert result.bot_id == "default"
        assert result.input == '[{"role": "user", "content": "hello"}]'


class TestBotOwnershipRules:
    """Test bot ownership validation rules per spec."""

    def test_owns_bot_returns_true_when_ac_bots_has_owner(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="user1", bot_id="bot-a")
        repo = BotChatDbRepository(db)

        assert repo.owns_bot("user1", "bot-a") is True

    def test_owns_bot_returns_false_for_missing_owner(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="user1", bot_id="bot-a")
        repo = BotChatDbRepository(db)

        assert repo.owns_bot("user2", "bot-a") is False

    def test_owns_bot_returns_false_for_soft_deleted_bot(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="user1", bot_id="bot-a", is_delete=1)
        repo = BotChatDbRepository(db)

        assert repo.owns_bot("user1", "bot-a") is False

    def test_owns_bot_returns_false_for_other_env(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="user1", bot_id="bot-a", env="prod")
        repo = BotChatDbRepository(db)

        assert repo.owns_bot("user1", "bot-a") is False

    def test_is_bot_collaborator_returns_true_for_current_env_record(self):
        db = SqliteTestDb()
        _insert_bot_collaborator(db, bot_id="bot-a", user_id="collaborator1", env="dev")
        repo = BotChatDbRepository(db)

        assert repo.is_bot_collaborator("collaborator1", "bot-a") is True

    def test_is_bot_collaborator_returns_false_for_other_env_record(self):
        db = SqliteTestDb()
        _insert_bot_collaborator(db, bot_id="bot-a", user_id="collaborator1", env="prod")
        repo = BotChatDbRepository(db)

        assert repo.is_bot_collaborator("collaborator1", "bot-a") is False

    def test_has_bot_access_returns_true_for_owner(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="owner1", bot_id="bot-a")
        repo = BotChatDbRepository(db)

        assert repo.has_bot_access("owner1", "bot-a") is True

    def test_has_bot_access_returns_true_for_collaborator(self):
        db = SqliteTestDb()
        _insert_bot_collaborator(db, bot_id="bot-a", user_id="collaborator1", env="dev")
        repo = BotChatDbRepository(db)

        assert repo.has_bot_access("collaborator1", "bot-a") is True

    def test_has_bot_access_returns_false_for_unrelated_user(self):
        db = SqliteTestDb()
        _insert_bot(db, entity_id="owner1", bot_id="bot-a")
        _insert_bot_collaborator(db, bot_id="bot-a", user_id="collaborator1", env="dev")
        repo = BotChatDbRepository(db)

        assert repo.has_bot_access("user2", "bot-a") is False


class TestBotIdQueryRules:
    """Test bot_id query rules per spec."""

    def test_no_bot_id_uses_user_id_filter(self):
        """When bot_id is not provided, filter by user_id = owner_id."""
        db = SqliteTestDb()
        _insert_trace(db, trace_id="owned-default", user_id="user1", bot_id="default")
        _insert_trace(db, trace_id="other-user", user_id="user2", bot_id="default")
        repo = BotChatDbRepository(db)

        sessions, total = repo.list_traces(
            owner_id="user1",
            from_ms=1780288428000,
            to_ms=1780288429000,
            page=1,
            limit=20,
        )

        assert total == 1
        assert [s.id for s in sessions] == ["owned-default"]

    def test_default_bot_uses_user_id_and_bot_id(self):
        """When bot_id='default', use user_id + bot_id='default'."""
        db = SqliteTestDb()
        _insert_trace(db, trace_id="default-owned", user_id="user1", bot_id="default")
        _insert_trace(db, trace_id="custom-owned", user_id="user1", bot_id="bot-a")
        repo = BotChatDbRepository(db)

        sessions, total = repo.list_traces(
            owner_id="user1",
            from_ms=1780288428000,
            to_ms=1780288429000,
            page=1,
            limit=20,
            bot_id="default",
        )

        assert total == 1
        assert [s.id for s in sessions] == ["default-owned"]

    def test_non_default_bot_uses_only_bot_id(self):
        """When bot_id!='default', only filter by bot_id (ownership checked separately)."""
        db = SqliteTestDb()
        _insert_trace(db, trace_id="custom-user1", user_id="user1", bot_id="bot-a")
        _insert_trace(db, trace_id="custom-user2", user_id="user2", bot_id="bot-a")
        _insert_trace(db, trace_id="other-bot", user_id="user1", bot_id="bot-b")
        repo = BotChatDbRepository(db)

        sessions, total = repo.list_traces(
            owner_id="user1",
            from_ms=1780288428000,
            to_ms=1780288429000,
            page=1,
            limit=20,
            bot_id="bot-a",
        )

        assert total == 2
        assert {s.id for s in sessions} == {"custom-user1", "custom-user2"}

    def test_list_traces_filters_session_fields_trace_query_and_paginates(self):
        db = SqliteTestDb()
        _insert_trace(
            db,
            trace_id="target-1",
            user_id="user1",
            session_id="session-key",
            real_session_id="real-session",
            name="Alpha target",
            gmt_trace=1780288428373,
        )
        _insert_trace(
            db,
            trace_id="target-2",
            user_id="user1",
            session_id="session-key",
            real_session_id="real-session",
            name="Beta target",
            gmt_trace=1780288428374,
        )
        _insert_trace(
            db,
            trace_id="filtered-out",
            user_id="user1",
            session_id="other-key",
            real_session_id="real-session",
            name="Alpha target",
            gmt_trace=1780288428375,
        )
        repo = BotChatDbRepository(db)

        sessions, total = repo.list_traces(
            owner_id="user1",
            from_ms=1780288428000,
            to_ms=1780288429000,
            page=2,
            limit=1,
            session_key="session-key",
            session_id="real-session",
            query="target",
        )

        assert total == 2
        assert len(sessions) == 1
        assert sessions[0].id == "target-1"

    def test_list_traces_filters_trace_id(self):
        db = SqliteTestDb()
        _insert_trace(db, trace_id="trace-a", user_id="user1")
        _insert_trace(db, trace_id="trace-b", user_id="user1")
        repo = BotChatDbRepository(db)

        sessions, total = repo.list_traces(
            owner_id="user1",
            from_ms=1780288428000,
            to_ms=1780288429000,
            page=1,
            limit=20,
            trace_id="trace-b",
        )

        assert total == 1
        assert sessions[0].id == "trace-b"
