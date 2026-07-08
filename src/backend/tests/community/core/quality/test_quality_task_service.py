"""Tests for QualityTaskService.

Tests cover all methods in QualityTaskService with mocked repository.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.quality.repositories import QualityTaskRecord
from agentclaw.community.core.quality.services.quality_task_service import QualityTaskService


def make_record(
    id: int = 1,
    uuid: str = "test-uuid",
    task_type: str = "eval",
    biz_type: str = "service_bot_single",
    status: str = "init",
    bot_id: str | None = None,
    owner_id: str | None = None,
    ext: dict | None = None,
    operator_id: str | None = None,
    env: str = "test",
    **kwargs,
) -> QualityTaskRecord:
    """Create a test QualityTaskRecord."""
    return QualityTaskRecord(
        id=id,
        uuid=uuid,
        task_type=task_type,
        biz_type=biz_type,
        status=status,
        bot_id=bot_id,
        owner_id=owner_id,
        ext=ext or {},
        operator_id=operator_id,
        env=env,
        gmt_create=kwargs.get("gmt_create", datetime.now()),
        gmt_modified=kwargs.get("gmt_modified", datetime.now()),
    )


class TestQualityTaskService:
    """Tests for QualityTaskService."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock repository."""
        return MagicMock()

    @pytest.fixture
    def service(self, mock_repo):
        """Create a service with mock repository."""
        return QualityTaskService(mock_repo)

    # ── list_tasks tests ──────────────────────────────────────────────────────

    def test_list_tasks_calls_repository_with_all_params(self, service, mock_repo):
        """Test list_tasks passes all params to repository."""
        mock_repo.list_by_conditions.return_value = ([], 0)

        result = service.list_tasks(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-123",
            owner_id="user-456",
            page=2,
            page_size=50,
        )

        mock_repo.list_by_conditions.assert_called_once_with(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-123",
            owner_id="user-456",
            page=2,
            page_size=50,
        )
        assert result == ([], 0)

    def test_list_tasks_with_minimal_params(self, service, mock_repo):
        """Test list_tasks with only required params."""
        records = [make_record(id=1), make_record(id=2)]
        mock_repo.list_by_conditions.return_value = (records, 2)

        result = service.list_tasks(
            task_type="stress_test",
            biz_type="multi_bot",
        )

        mock_repo.list_by_conditions.assert_called_once_with(
            task_type="stress_test",
            biz_type="multi_bot",
            bot_id=None,
            owner_id=None,
            page=1,
            page_size=20,
        )
        assert result == (records, 2)

    def test_list_tasks_returns_repository_result(self, service, mock_repo):
        """Test list_tasks returns repository result directly."""
        records = [make_record(id=1), make_record(id=2), make_record(id=3)]
        mock_repo.list_by_conditions.return_value = (records, 3)

        result, total = service.list_tasks(
            task_type="eval",
            biz_type="service_bot_single",
        )

        assert len(result) == 3
        assert total == 3
        assert result == records

    # ── get_task_by_uuid tests ────────────────────────────────────────────────

    def test_get_task_by_uuid_found(self, service, mock_repo):
        """Test get_task_by_uuid returns task when found."""
        record = make_record(id=1, uuid="test-uuid-123")
        mock_repo.get_by_uuid.return_value = record

        result = service.get_task_by_uuid("test-uuid-123")

        mock_repo.get_by_uuid.assert_called_once_with("test-uuid-123")
        assert result == record
        assert result.uuid == "test-uuid-123"

    def test_get_task_by_uuid_not_found(self, service, mock_repo):
        """Test get_task_by_uuid returns None when not found."""
        mock_repo.get_by_uuid.return_value = None

        result = service.get_task_by_uuid("nonexistent-uuid")

        mock_repo.get_by_uuid.assert_called_once_with("nonexistent-uuid")
        assert result is None

    # ── get_task_by_id tests ──────────────────────────────────────────────────

    def test_get_task_by_id_found(self, service, mock_repo):
        """Test get_task_by_id returns task when found."""
        record = make_record(id=42, uuid="test-uuid-42")
        mock_repo.get_by_id.return_value = record

        result = service.get_task_by_id(42)

        mock_repo.get_by_id.assert_called_once_with(42)
        assert result == record
        assert result.id == 42

    def test_get_task_by_id_not_found(self, service, mock_repo):
        """Test get_task_by_id returns None when not found."""
        mock_repo.get_by_id.return_value = None

        result = service.get_task_by_id(999)

        mock_repo.get_by_id.assert_called_once_with(999)
        assert result is None

    # ── create_task tests ─────────────────────────────────────────────────────

    def test_create_task_generates_uuid_hex_format(self, service, mock_repo):
        """Test create_task generates UUID in hex format (no hyphens)."""
        created_record = make_record(id=1, uuid="a1b2c3d4e5f67890a1b2c3d4e5f67890")
        mock_repo.create.return_value = created_record

        with patch("agentclaw.community.core.quality.services.quality_task_service.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "a1b2c3d4e5f67890a1b2c3d4e5f67890"
            result = service.create_task(
                task_type="eval",
                biz_type="service_bot_single",
            )

        # Verify UUID was generated using .hex (32 chars, no hyphens)
        mock_uuid.assert_called_once()
        mock_repo.create.assert_called_once_with(
            uuid="a1b2c3d4e5f67890a1b2c3d4e5f67890",
            task_type="eval",
            biz_type="service_bot_single",
            bot_id=None,
            owner_id=None,
            ext=None,
            operator_id=None,
        )
        assert result == created_record

    def test_create_task_uuid_is_32_char_hex(self, service, mock_repo):
        """Test generated UUID is 32-character hex string without hyphens."""
        mock_repo.create.return_value = make_record(id=1)

        service.create_task(task_type="eval", biz_type="service_bot_single")

        call_kwargs = mock_repo.create.call_args[1]
        uuid_value = call_kwargs["uuid"]

        # UUID hex format: 32 hex chars, no hyphens
        assert len(uuid_value) == 32
        assert "-" not in uuid_value
        # Should be valid hex
        int(uuid_value, 16)

    def test_create_task_with_all_params(self, service, mock_repo):
        """Test create_task passes all params to repository."""
        created_record = make_record(
            id=1,
            bot_id="bot-123",
            owner_id="user-456",
            ext={"key": "value"},
            operator_id="op-789",
        )
        mock_repo.create.return_value = created_record

        result = service.create_task(
            task_type="eval",
            biz_type="service_bot_single",
            bot_id="bot-123",
            owner_id="user-456",
            ext={"key": "value"},
            operator_id="op-789",
        )

        # Verify repository was called with all params
        call_kwargs = mock_repo.create.call_args[1]
        assert call_kwargs["task_type"] == "eval"
        assert call_kwargs["biz_type"] == "service_bot_single"
        assert call_kwargs["bot_id"] == "bot-123"
        assert call_kwargs["owner_id"] == "user-456"
        assert call_kwargs["ext"] == {"key": "value"}
        assert call_kwargs["operator_id"] == "op-789"
        # UUID should be generated
        assert "uuid" in call_kwargs
        assert call_kwargs["uuid"] is not None

        assert result == created_record

    def test_create_task_returns_created_record(self, service, mock_repo):
        """Test create_task returns the created record."""
        created_record = make_record(id=123, uuid="new-uuid", status="init")
        mock_repo.create.return_value = created_record

        result = service.create_task(
            task_type="stress_test",
            biz_type="multi_bot",
        )

        assert result.id == 123
        assert result.status == "init"

    # ── update_task_status tests ──────────────────────────────────────────────

    def test_update_task_status_success(self, service, mock_repo):
        """Test update_task_status updates status correctly."""
        updated_record = make_record(id=1, status="running")
        mock_repo.update_status.return_value = updated_record

        result = service.update_task_status(1, "running")

        mock_repo.update_status.assert_called_once_with(1, "running", None)
        assert result == updated_record
        assert result.status == "running"

    def test_update_task_status_with_ext(self, service, mock_repo):
        """Test update_task_status passes ext to repository."""
        updated_record = make_record(id=1, status="success", ext={"result": "passed"})
        mock_repo.update_status.return_value = updated_record

        result = service.update_task_status(
            1, "success", {"result": "passed", "score": 95}
        )

        mock_repo.update_status.assert_called_once_with(
            1, "success", {"result": "passed", "score": 95}
        )
        assert result == updated_record

    def test_update_task_status_not_found(self, service, mock_repo):
        """Test update_task_status returns None when not found."""
        mock_repo.update_status.return_value = None

        result = service.update_task_status(999, "running")

        mock_repo.update_status.assert_called_once_with(999, "running", None)
        assert result is None

    def test_update_task_status_with_none_ext(self, service, mock_repo):
        """Test update_task_status with explicit None ext."""
        updated_record = make_record(id=1, status="failed")
        mock_repo.update_status.return_value = updated_record

        result = service.update_task_status(1, "failed", None)

        mock_repo.update_status.assert_called_once_with(1, "failed", None)
        assert result == updated_record