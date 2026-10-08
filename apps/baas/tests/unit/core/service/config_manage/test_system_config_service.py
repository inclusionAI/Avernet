"""Unit tests for DefaultSystemConfigManageService.

Covers all public methods: create_config, get_config, update_config,
delete_config, list_configs.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.config_manage import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from secbaas.community.core.repository.system_config import SystemConfigRecord

# ==================== Fixtures ====================


@pytest.fixture
def mock_env():
    """Mock get_current_env in the service module."""
    with patch(
        "secbaas.community.core.service.config_manage._system_config_service.get_current_env",
        return_value="test",
    ):
        yield


@pytest.fixture
def mock_repo():
    """Mock SystemConfigRepository."""
    return MagicMock()


@pytest.fixture
def service(mock_env, mock_repo):
    from secbaas.community.core.service.config_manage import (
        DefaultSystemConfigManageService,
    )

    return DefaultSystemConfigManageService(repository=mock_repo)


def _make_record(
    record_id: int = 1,
    conf_key: str = "test.key",
    conf_value: str = "test-value",
    env: str = "test",
    name: str = "Test Config",
    description: str | None = None,
) -> SystemConfigRecord:
    """Create a minimal SystemConfigRecord stub."""
    record = MagicMock(spec=SystemConfigRecord)
    record.id = record_id
    record.conf_key = conf_key
    record.conf_value = conf_value
    record.env = env
    record.name = name
    record.description = description
    record.creator = "admin"
    record.modifier = "admin"
    record.gmt_create = datetime(2024, 1, 1)
    record.gmt_modified = datetime(2024, 1, 1)
    return record


# ==================== Test create_config ====================


class TestCreateConfig:
    """Tests for DefaultSystemConfigManageService.create_config."""

    def test_success(self, mock_env, mock_repo, service):
        mock_repo.insert_config.return_value = 1
        mock_repo.get_by_id.return_value = _make_record(record_id=1)

        data = SystemConfigCreate(
            conf_key="test.key",
            conf_value="value",
            name="Test",
            operator="admin",
        )
        result = service.create_config(data)

        assert isinstance(result, SystemConfigResponse)
        assert result.conf_key == "test.key"
        mock_repo.insert_config.assert_called_once()

    def test_raises_if_operator_missing(self, mock_env, mock_repo, service):
        data = SystemConfigCreate(
            conf_key="test.key",
            conf_value="value",
            name="Test",
            operator=None,
        )
        with pytest.raises(ValueError, match="operator is required"):
            service.create_config(data)
        mock_repo.insert_config.assert_not_called()


# ==================== Test get_config ====================


class TestGetConfig:
    """Tests for DefaultSystemConfigManageService.get_config."""

    def test_found(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = _make_record()

        result = service.get_config("test.key")

        assert result is not None
        assert result.conf_key == "test.key"
        mock_repo.get_by_env_and_key.assert_called_once_with("test", "test.key")

    def test_not_found(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = None

        result = service.get_config("nonexistent.key")

        assert result is None


# ==================== Test update_config ====================


class TestUpdateConfig:
    """Tests for DefaultSystemConfigManageService.update_config."""

    def test_success(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.side_effect = [
            _make_record(record_id=1),  # first read: exists
            _make_record(record_id=1, conf_value="new-value"),  # second read: updated
        ]

        data = SystemConfigUpdate(conf_value="new-value", operator="admin")
        result = service.update_config("test.key", data)

        assert result is not None
        assert result.conf_value == "new-value"
        mock_repo.update_config.assert_called_once()

    def test_not_found(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = None

        data = SystemConfigUpdate(conf_value="new-value", operator="admin")
        result = service.update_config("test.key", data)

        assert result is None
        mock_repo.update_config.assert_not_called()

    def test_no_updates_skips_repo(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = _make_record(record_id=1)

        data = SystemConfigUpdate(operator="admin")
        result = service.update_config("test.key", data)

        assert result is not None
        mock_repo.update_config.assert_not_called()


# ==================== Test delete_config ====================


class TestDeleteConfig:
    """Tests for DefaultSystemConfigManageService.delete_config."""

    def test_success(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = _make_record(record_id=1)

        result = service.delete_config("test.key")

        assert result is True
        mock_repo.delete_config.assert_called_once_with(config_id=1)

    def test_not_found(self, mock_env, mock_repo, service):
        mock_repo.get_by_env_and_key.return_value = None

        result = service.delete_config("nonexistent.key")

        assert result is False
        mock_repo.delete_config.assert_not_called()


# ==================== Test list_configs ====================


class TestListConfigs:
    """Tests for DefaultSystemConfigManageService.list_configs."""

    def test_with_results(self, mock_env, mock_repo, service):
        record = _make_record(record_id=1, conf_key="test.key")
        mock_repo.list_configs.return_value = (1, [record])

        result = service.list_configs(page=1, page_size=20)

        assert isinstance(result, SystemConfigListResponse)
        assert len(result.items) == 1
        assert result.total == 1
        assert result.page == 1
        assert result.page_size == 20
        mock_repo.list_configs.assert_called_once_with(env="test", page=1, page_size=20)

    def test_empty(self, mock_env, mock_repo, service):
        mock_repo.list_configs.return_value = (0, [])

        result = service.list_configs(page=1, page_size=20)

        assert len(result.items) == 0
        assert result.total == 0
