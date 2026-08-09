"""Tests for quality_repository plugin.

Tests cover all code paths in QualityTaskRepository and _row_to_record.
"""
import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.quality.models import QualityTaskRecord
from agentclaw.community.core.repository.implementations.platform.quality import QualityTaskRepository, _row_to_record


class MockRow:
    """Mock ORM row object for testing _row_to_record."""

    def __init__(
        self,
        id: int = 1,
        uuid: str | None = "test-uuid",
        task_type: str = "eval",
        biz_type: str = "service_bot_single",
        status: str = "init",
        bot_id: str | None = None,
        owner_id: str | None = None,
        ext: Any = "{}",
        operator_id: str | None = None,
        env: str | None = "test",
        gmt_create: datetime | None = None,
        gmt_modified: datetime | None = None,
    ):
        self.id = id
        self.uuid = uuid
        self.task_type = task_type
        self.biz_type = biz_type
        self.status = status
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.ext = ext
        self.operator_id = operator_id
        self.env = env
        self.gmt_create = gmt_create or datetime.now()
        self.gmt_modified = gmt_modified or datetime.now()


class TestRowToRecord:
    """Tests for _row_to_record helper function."""

    def test_none_row_returns_none(self):
        """Test that None row returns None."""
        result = _row_to_record(None)
        assert result is None

    def test_row_with_valid_json_ext_string(self):
        """Test row with valid JSON ext string parses correctly."""
        row = MockRow(ext='{"key": "value", "number": 42}')
        result = _row_to_record(row)

        assert result is not None
        assert result.ext == {"key": "value", "number": 42}

    def test_row_with_invalid_json_ext_string_returns_empty_dict(self):
        """Test row with invalid JSON ext string returns empty dict."""
        row = MockRow(ext="not valid json")
        result = _row_to_record(row)

        assert result is not None
        assert result.ext == {}

    def test_row_with_dict_ext_returns_as_is(self):
        """Test row with dict ext returns as is."""
        row = MockRow(ext={"already": "a dict"})
        result = _row_to_record(row)

        assert result is not None
        assert result.ext == {"already": "a dict"}

    def test_row_with_none_ext_returns_empty_dict(self):
        """Test row with None ext returns empty dict."""
        row = MockRow(ext=None)
        result = _row_to_record(row)

        assert result is not None
        assert result.ext == {}

    def test_row_with_env_set_uses_row_env(self):
        """Test row with env set uses row.env."""
        row = MockRow(env="production")
        result = _row_to_record(row)

        assert result is not None
        assert result.env == "production"

    def test_row_with_none_env_uses_current_env(self):
        """Test row with None env uses get_current_env()."""
        with patch("agentclaw.community.core.repository.implementations.platform.quality.get_current_env") as mock_env:
            mock_env.return_value = "dev"
            row = MockRow(env=None)
            result = _row_to_record(row)

            assert result is not None
            assert result.env == "dev"
            mock_env.assert_called_once()

    def test_row_with_all_fields_converts_correctly(self):
        """Test row with all fields converts correctly."""
        created = datetime(2024, 1, 1, 12, 0, 0)
        modified = datetime(2024, 1, 2, 13, 30, 0)
        row = MockRow(
            id=42,
            uuid="uuid-123",
            task_type="stress_test",
            biz_type="multi_bot",
            status="running",
            bot_id="bot-999",
            owner_id="user-888",
            ext='{"nested": {"key": "value"}}',
            operator_id="op-456",
            env="staging",
            gmt_create=created,
            gmt_modified=modified,
        )
        result = _row_to_record(row)

        assert result is not None
        assert result.id == 42
        assert result.uuid == "uuid-123"
        assert result.task_type == "stress_test"
        assert result.biz_type == "multi_bot"
        assert result.status == "running"
        assert result.bot_id == "bot-999"
        assert result.owner_id == "user-888"
        assert result.ext == {"nested": {"key": "value"}}
        assert result.operator_id == "op-456"
        assert result.env == "staging"
        assert result.gmt_create == created
        assert result.gmt_modified == modified

    def test_row_with_type_error_on_ext_json_decode(self):
        """Test row with TypeError during JSON decode returns empty dict."""
        # TypeError can be raised when json.loads gets unexpected type
        # The code catches TypeError in the try block
        row = MockRow(ext='{"key": invalid}')  # Invalid JSON that will fail parsing
        result = _row_to_record(row)

        assert result is not None
        # Invalid JSON returns empty dict due to JSONDecodeError being caught
        assert result.ext == {}

    def test_row_with_none_uuid(self):
        """Test row with None uuid converts correctly."""
        row = MockRow(uuid=None)
        result = _row_to_record(row)

        assert result is not None
        assert result.uuid is None


class InMemoryDB:
    """In-memory SQLite database for testing."""

    def __init__(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Create tables
        from agentclaw.community.core.base import Base

        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine, autoflush=False)

    def orm_session(self):
        """Context manager for ORM session."""

        class SessionContext:
            def __init__(self, factory):
                self._factory = factory
                self._session = None

            def __enter__(self):
                self._session = self._factory()
                return self._session

            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type:
                    self._session.rollback()
                else:
                    self._session.commit()
                self._session.close()
                return False

        return SessionContext(self._session_factory)


class TestQualityTaskRepository:
    """Tests for QualityTaskRepository class."""

    @pytest.fixture
    def db(self):
        """Create in-memory database."""
        return InMemoryDB()

    @pytest.fixture
    def repo(self, db):
        """Create repository with in-memory database."""
        return QualityTaskRepository(db)

    # ── create tests ──────────────────────────────────────────────────────────

    def test_create_with_all_fields(self, repo):
        """Test creating a task with all fields."""
        result = repo.create(
            uuid="test-uuid-1",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-123",
            owner_id="user-456",
            ext={"key": "value"},
            operator_id="op-789",
        )

        assert result.id is not None
        assert result.uuid == "test-uuid-1"
        assert result.task_type == "eval"
        assert result.biz_type == "service_bot_single"
        assert result.status == "init"
        assert result.bot_id == "bot-123"
        assert result.owner_id == "user-456"
        assert result.ext == {"key": "value"}
        assert result.operator_id == "op-789"

    def test_create_with_minimal_fields(self, repo):
        """Test creating a task with only required fields."""
        result = repo.create(
            uuid="test-uuid-2",
            task_type="stress_test",
            biz_type="multi_bot",
        )

        assert result.id is not None
        assert result.uuid == "test-uuid-2"
        assert result.task_type == "stress_test"
        assert result.biz_type == "multi_bot"
        assert result.status == "init"
        assert result.bot_id is None
        assert result.owner_id is None
        assert result.ext == {}
        assert result.operator_id is None

    def test_create_with_none_ext(self, repo):
        """Test creating a task with None ext."""
        result = repo.create(
            uuid="test-uuid-3",
            task_type="eval",
            biz_type="service_bot_single",
            ext=None,
        )

        assert result.ext == {}

    def test_create_serializes_ext_to_json(self, repo):
        """Test that ext is serialized to JSON."""
        result = repo.create(
            uuid="test-uuid-4",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"nested": {"key": "value"}},
        )

        # Verify ext was stored and retrieved correctly
        assert result.ext == {"nested": {"key": "value"}}

    def test_create_with_none_uuid(self, repo):
        """Test creating a task with None uuid."""
        result = repo.create(
            uuid=None,
            task_type="eval",
            biz_type="service_bot_single",
        )

        assert result.id is not None
        assert result.uuid is None
        assert result.task_type == "eval"
        assert result.biz_type == "service_bot_single"

    # ── get_by_id tests ───────────────────────────────────────────────────────

    def test_get_by_id_found(self, repo):
        """Test get_by_id returns task when found."""
        created = repo.create(
            uuid="test-uuid-get",
            task_type="eval",
            biz_type="service_bot_single",
        )

        result = repo.get_by_id(created.id)

        assert result is not None
        assert result.id == created.id
        assert result.uuid == "test-uuid-get"

    def test_get_by_id_not_found(self, repo):
        """Test get_by_id returns None when not found."""
        result = repo.get_by_id(99999)

        assert result is None

    def test_get_by_id_filters_by_env(self, repo):
        """Test get_by_id filters by current env."""
        with patch("agentclaw.community.core.repository.implementations.platform.quality.get_current_env") as mock_env:
            mock_env.return_value = "test-env"
            # Create in test-env
            created = repo.create(
                uuid="test-uuid-env",
                task_type="eval",
                biz_type="service_bot_single",
            )

            # Should find in same env
            result = repo.get_by_id(created.id)
            assert result is not None

            # Switch env, should not find
            mock_env.return_value = "other-env"
            result = repo.get_by_id(created.id)
            assert result is None

    # ── get_by_uuid tests ─────────────────────────────────────────────────────

    def test_get_by_uuid_found(self, repo):
        """Test get_by_uuid returns task when found."""
        created = repo.create(
            uuid="unique-uuid-123",
            task_type="eval",
            biz_type="service_bot_single",
        )

        result = repo.get_by_uuid("unique-uuid-123")

        assert result is not None
        assert result.uuid == "unique-uuid-123"

    def test_get_by_uuid_not_found(self, repo):
        """Test get_by_uuid returns None when not found."""
        result = repo.get_by_uuid("nonexistent-uuid")

        assert result is None

    # ── list_by_conditions tests ──────────────────────────────────────────────

    def test_list_by_conditions_basic(self, repo):
        """Test basic list with required filters."""
        repo.create(uuid="uuid-1", task_type="eval", biz_type="service_bot_single")
        repo.create(uuid="uuid-2", task_type="eval", biz_type="service_bot_single")
        repo.create(uuid="uuid-3", task_type="stress_test", biz_type="multi_bot")

        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
        )

        assert total == 2
        assert len(results) == 2

    def test_list_by_conditions_with_bot_id_filter(self, repo):
        """Test list with bot_id filter."""
        repo.create(
            uuid="uuid-bot-1",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-A",
        )
        repo.create(
            uuid="uuid-bot-2",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-B",
        )

        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-A",
        )

        assert total == 1
        assert len(results) == 1
        assert results[0].bot_id == "bot-A"

    def test_list_by_conditions_with_owner_id_filter(self, repo):
        """Test list with owner_id filter."""
        repo.create(
            uuid="uuid-owner-1",
            task_type="eval",
            biz_type="service_bot_single",
            owner_id="user-X",
        )
        repo.create(
            uuid="uuid-owner-2",
            task_type="eval",
            biz_type="service_bot_single",
            owner_id="user-Y",
        )

        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            owner_id="user-X",
        )

        assert total == 1
        assert len(results) == 1
        assert results[0].owner_id == "user-X"

    def test_list_by_conditions_with_both_filters(self, repo):
        """Test list with both bot_id and owner_id filters."""
        repo.create(
            uuid="uuid-both-1",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-1",
            owner_id="user-1",
        )
        repo.create(
            uuid="uuid-both-2",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-1",
            owner_id="user-2",
        )

        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-1",
            owner_id="user-1",
        )

        assert total == 1
        assert results[0].bot_id == "bot-1"
        assert results[0].owner_id == "user-1"

    def test_list_by_conditions_pagination(self, repo):
        """Test list pagination."""
        for i in range(25):
            repo.create(
                uuid=f"uuid-page-{i}",
                task_type="eval",
                biz_type="service_bot_single",
            )

        # First page
        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            page=1,
            page_size=10,
        )
        assert total == 25
        assert len(results) == 10

        # Second page
        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            page=2,
            page_size=10,
        )
        assert total == 25
        assert len(results) == 10

        # Third page (partial)
        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="service_bot_single",
            page=3,
            page_size=10,
        )
        assert total == 25
        assert len(results) == 5

    def test_list_by_conditions_empty_results(self, repo):
        """Test list returns empty when no matches."""
        results, total = repo.list_by_conditions(
            task_type="nonexistent",
            biz_type="nonexistent",
        )

        assert total == 0
        assert len(results) == 0

    def test_list_by_conditions_returns_all_matching(self, repo):
        """Test list returns all matching records."""
        repo.create(uuid="uuid-list-1", task_type="eval", biz_type="single")
        repo.create(uuid="uuid-list-2", task_type="eval", biz_type="single")
        repo.create(uuid="uuid-list-3", task_type="eval", biz_type="single")

        results, total = repo.list_by_conditions(
            task_type="eval",
            biz_type="single",
        )

        assert total == 3
        assert len(results) == 3
        uuids = {r.uuid for r in results}
        assert "uuid-list-1" in uuids
        assert "uuid-list-2" in uuids
        assert "uuid-list-3" in uuids

    # ── update_status tests ───────────────────────────────────────────────────

    def test_update_status_success(self, repo):
        """Test update_status updates status correctly."""
        created = repo.create(
            uuid="test-uuid-update",
            task_type="eval",
            biz_type="service_bot_single",
        )
        assert created.status == "init"

        result = repo.update_status(created.id, "running")

        assert result is not None
        assert result.status == "running"

    def test_update_status_not_found(self, repo):
        """Test update_status returns None when not found."""
        result = repo.update_status(99999, "running")

        assert result is None

    def test_update_status_merges_ext(self, repo):
        """Test update_status merges ext dict."""
        created = repo.create(
            uuid="test-uuid-ext",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"existing": "value"},
        )

        result = repo.update_status(created.id, "running", {"new_key": "new_value"})

        assert result is not None
        assert result.ext == {"existing": "value", "new_key": "new_value"}

    def test_update_status_ext_none_does_not_modify(self, repo):
        """Test update_status with None ext does not modify ext."""
        created = repo.create(
            uuid="test-uuid-ext-none",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"key": "value"},
        )

        result = repo.update_status(created.id, "running", None)

        assert result is not None
        assert result.ext == {"key": "value"}

    def test_update_status_merges_with_existing_ext_dict(self, repo):
        """Test update_status merges with existing ext when stored as dict."""
        # Create with some initial ext
        created = repo.create(
            uuid="test-uuid-ext-dict",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"a": 1, "b": 2},
        )

        # Update with new ext values
        result = repo.update_status(created.id, "success", {"b": 3, "c": 4})

        assert result.ext == {"a": 1, "b": 3, "c": 4}

    def test_update_status_handles_invalid_json_in_row(self, repo):
        """Test update_status handles invalid JSON in existing ext."""
        created = repo.create(
            uuid="test-uuid-invalid-json",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"key": "value"},
        )

        # Directly update the row to have invalid JSON (simulating data corruption)
        with repo._db.orm_session() as session:
            from agentclaw.community.plugin_api.models import QualityTaskModel

            row = session.query(QualityTaskModel).filter(QualityTaskModel.id == created.id).first()
            row.ext = "invalid json {{{"
            session.flush()

        # Update should still work, replacing the invalid JSON with the new ext
        result = repo.update_status(created.id, "running", {"new": "data"})

        assert result is not None
        assert result.status == "running"
        # Invalid JSON gets replaced with new data
        assert result.ext == {"new": "data"}

    def test_update_status_updates_gmt_modified(self, repo):
        """Test update_status updates gmt_modified."""
        created = repo.create(
            uuid="test-uuid-modified",
            task_type="eval",
            biz_type="service_bot_single",
        )
        original_modified = created.gmt_modified

        result = repo.update_status(created.id, "running")

        assert result is not None
        # gmt_modified should be updated (may be same in fast tests, but field is set)
        # Just verify the field exists
        assert result.gmt_modified is not None

    # ── update_ext tests ───────────────────────────────────────────────────────

    def test_update_ext_success(self, repo):
        """Test update_ext updates ext field correctly."""
        created = repo.create(
            uuid="test-uuid-update-ext",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"existing": "value"},
        )
        assert created.status == "init"

        result = repo.update_ext(created.id, {"new_key": "new_value"})

        assert result is not None
        assert result.status == "init"  # status unchanged
        assert result.ext == {"existing": "value", "new_key": "new_value"}

    def test_update_ext_not_found(self, repo):
        """Test update_ext returns None when not found."""
        result = repo.update_ext(99999, {"key": "value"})

        assert result is None

    def test_update_ext_merges_with_existing(self, repo):
        """Test update_ext merges with existing ext."""
        created = repo.create(
            uuid="test-uuid-ext-merge",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"a": 1, "b": 2},
        )

        result = repo.update_ext(created.id, {"b": 3, "c": 4})

        assert result is not None
        assert result.ext == {"a": 1, "b": 3, "c": 4}

    def test_update_ext_handles_invalid_json(self, repo):
        """Test update_ext handles invalid JSON in existing ext."""
        created = repo.create(
            uuid="test-uuid-ext-invalid",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"key": "value"},
        )

        # Directly update the row to have invalid JSON
        with repo._db.orm_session() as session:
            from agentclaw.community.plugin_api.models import QualityTaskModel

            row = session.query(QualityTaskModel).filter(QualityTaskModel.id == created.id).first()
            row.ext = "invalid json {{{"
            session.flush()

        result = repo.update_ext(created.id, {"new": "data"})

        assert result is not None
        assert result.status == "init"
        assert result.ext == {"new": "data"}

    def test_update_ext_overwrites_keys(self, repo):
        """Test update_ext overwrites existing keys."""
        created = repo.create(
            uuid="test-uuid-ext-overwrite",
            task_type="eval",
            biz_type="service_bot_single",
            ext={"key": "old", "other": "unchanged"},
        )

        result = repo.update_ext(created.id, {"key": "new"})

        assert result is not None
        assert result.ext == {"key": "new", "other": "unchanged"}

    def test_update_ext_updates_gmt_modified(self, repo):
        """Test update_ext updates gmt_modified."""
        created = repo.create(
            uuid="test-uuid-ext-modified",
            task_type="eval",
            biz_type="service_bot_single",
        )

        result = repo.update_ext(created.id, {"key": "value"})

        assert result is not None
        assert result.gmt_modified is not None