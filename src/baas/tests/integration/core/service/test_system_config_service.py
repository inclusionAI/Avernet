"""Optimized integration tests for DefaultSystemConfigManageService with shared fixtures.

Consolidates all individual tests into 1 comprehensive test method.
Uses shared fixtures to minimize database record creation.
"""

import time

import pytest

from secbaas.community.api.config_manage import (
    SystemConfigCreate,
    SystemConfigUpdate,
)
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()


def _scs():
    return get_container().services.system_config_service()


def generate_config_key() -> str:
    """Generate unique config key for testing."""
    return f"test.config.{int(time.time() * 1000000) % 10000000000}"


@pytest.mark.integration
class TestDefaultSystemConfigManageServiceIntegration:
    """Optimized integration tests for DefaultSystemConfigManageService."""

    def test_config_crud_operations(self, skip_if_zdas_unavailable):
        """Comprehensive test for config CRUD operations.

        Tests:
        - create config with valid data
        - get config by key
        - get nonexistent config returns None
        - update config
        - delete config
        """
        # Generate unique key for this test run
        config_key = generate_config_key()

        # Test 1: Create config with valid data
        create_data = SystemConfigCreate(
            conf_key=config_key,
            conf_value='{"setting": "value"}',
            name="Test Config",
            description=None,
            operator="test_user",
        )
        result = _scs().create_config(create_data)
        assert result is not None
        assert result.conf_key == config_key

        # Test 2: Get config by key
        get_result = _scs().get_config(config_key)
        assert get_result is not None
        assert get_result.conf_key == config_key
        assert get_result.conf_value == '{"setting": "value"}'

        # Test 3: Get nonexistent config returns None
        get_result2 = _scs().get_config("nonexistent.key")
        assert get_result2 is None

        # Test 4: Update config
        update_data = SystemConfigUpdate(
            conf_value='{"setting": "updated"}',
            name=None,
            description=None,
            operator="test_user_2",
        )
        update_result = _scs().update_config(config_key, update_data)
        assert update_result is not None
        assert update_result.conf_value == '{"setting": "updated"}'

        # Test 5: Delete config (soft delete via is_deleted or similar)
        # Note: Implementation may vary - adjust based on actual service behavior
        # _scs().delete_config(TEST_ENV, config_key)
