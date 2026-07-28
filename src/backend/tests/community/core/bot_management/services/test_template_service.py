"""Unit tests for template_service.py."""
import pytest
from unittest.mock import Mock

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
    TemplateValidationError,
    TemplateServiceError,
)


class TestTemplateService:
    """Test cases for TemplateService."""

    @pytest.fixture
    def template_service(self):
        """Create a TemplateService instance with mocked repository."""
        mock_repo = Mock()
        service = TemplateService(repository=mock_repo, vault=TokenVault(master_key=""))
        yield service

    def test_validate_ext_content_with_valid_dict(self, template_service):
        """Test _validate_ext_content with valid dictionary."""
        ext_content = {
            "name": "test-template",
            "version": "1.0.0",
            "template_type": "applicationCoding",
            "coding_config": {
                "language": "python",
                "framework": "fastapi"
            }
        }
        # Should not raise any exception
        template_service._validate_ext_content(ext_content)

    def test_validate_ext_content_with_empty_dict(self, template_service):
        """Test _validate_ext_content with empty dictionary raises error."""
        with pytest.raises(TemplateValidationError, match="Template ext content cannot be empty"):
            template_service._validate_ext_content({})

    def test_validate_ext_content_with_non_dict(self, template_service):
        """Test _validate_ext_content with non-dict raises error."""
        with pytest.raises(TemplateValidationError, match="Template ext content must be a dictionary"):
            template_service._validate_ext_content("not a dict")

    def test_validate_ext_content_with_none(self, template_service):
        """Test _validate_ext_content with None raises error."""
        with pytest.raises(TemplateValidationError, match="Template ext content must be a dictionary"):
            template_service._validate_ext_content(None)

    def test_validate_ext_content_with_ext_without_name_version(self, template_service):
        """Test _validate_ext_content accepts ext without name/version fields."""
        ext_content = {
            "template_type": "applicationCoding",
            "coding_config": {
                "language": "python",
                "framework": "fastapi"
            }
            # No name or version fields - should be allowed
        }
        # Should not raise any exception
        template_service._validate_ext_content(ext_content)

    def test_validate_ext_content_with_nested_dict(self, template_service):
        """Test _validate_ext_content accepts nested dictionaries."""
        ext_content = {
            "name": "test",
            "nested": {
                "key": "value",
                "list": [1, 2, 3]
            }
        }
        # Should not raise any exception for valid nested content
        template_service._validate_ext_content(ext_content)

    def test_create_template_success(self, template_service):
        """Test create_template succeeds with valid config."""
        bot_id = "test-bot-id"
        template_config = {
            "name": "test-template",
            "version": "1.0.0",
            "template_type": "applicationCoding",
            "coding_config": {
                "language": "python",
                "framework": "fastapi"
            }
        }

        # Mock repository insert
        template_service._repository.insert.return_value = {
            "bot_id": bot_id,
            "ext": template_config
        }

        result = template_service.create_template(bot_id, template_config)

        assert result["bot_id"] == bot_id
        assert result["ext"] == template_config
        template_service._repository.insert.assert_called_once()

    def test_create_template_missing_config(self, template_service):
        """Test create_template raises error when config is missing."""
        bot_id = "test-bot-id"

        with pytest.raises(TemplateValidationError, match="Template ext content must be a dictionary"):
            template_service.create_template(bot_id, None)

    def test_create_template_empty_config(self, template_service):
        """Test create_template raises error when config is empty."""
        bot_id = "test-bot-id"
        template_config = {}

        with pytest.raises(TemplateValidationError, match="Template ext content cannot be empty"):
            template_service.create_template(bot_id, template_config)

    def test_update_template_success(self, template_service):
        """Test update_template succeeds with valid config."""
        bot_id = "test-bot-id"
        template_config = {
            "name": "updated-template",
            "template_type": "applicationCoding",
            "coding_config": {
                "language": "python",
                "framework": "django"
            }
        }

        # Mock repository methods
        template_service._repository.exists_by_bot_id.return_value = True
        template_service._repository.update_by_bot_id.return_value = {
            "bot_id": bot_id,
            "ext": template_config
        }

        result = template_service.update_template(bot_id, template_config)

        assert result["bot_id"] == bot_id
        assert result["ext"] == template_config

    def test_get_template_config_success(self, template_service):
        """Test get_template_config returns ext content."""
        bot_id = "test-bot-id"
        ext_content = {
            "name": "test-template",
            "template_type": "applicationCoding",
            "coding_config": {
                "language": "python",
                "framework": "fastapi"
            }
        }

        template_service._repository.get_by_bot_id.return_value = {
            "bot_id": bot_id,
            "ext": ext_content
        }

        result = template_service.get_template_config(bot_id)

        assert result == ext_content

    def test_validate_ext_content_with_nested_dict_ok(self, template_service):
        """Test _validate_ext_content handles nested dictionaries correctly."""
        ext_content = {
            "level1": {
                "level2": {
                    "level3": "value"
                }
            },
            "list_with_dicts": [
                {"item": 1},
                {"item": 2}
            ]
        }
        # Should not raise any exception
        template_service._validate_ext_content(ext_content)


def _make_template_service_for_list_by_bot_ids():
    repo = Mock()
    return TemplateService(repository=repo, vault=TokenVault(master_key=""))


def test_list_templates_by_bot_ids_empty_returns_empty():
    template_service = _make_template_service_for_list_by_bot_ids()

    assert template_service.list_templates_by_bot_ids([]) == []
    template_service._repository.list_by_bot_ids.assert_not_called()


def test_list_templates_by_bot_ids_delegates_to_repository():
    template_service = _make_template_service_for_list_by_bot_ids()
    template_service._repository.list_by_bot_ids.return_value = [{"bot_id": "b1", "ext": {}}]

    assert template_service.list_templates_by_bot_ids(["b1"]) == [{"bot_id": "b1", "ext": {}}]
    template_service._repository.list_by_bot_ids.assert_called_once_with(["b1"])


def test_list_templates_by_bot_ids_repository_failure_returns_empty():
    template_service = _make_template_service_for_list_by_bot_ids()
    template_service._repository.list_by_bot_ids.side_effect = RuntimeError("boom")

    assert template_service.list_templates_by_bot_ids(["b1"]) == []
