"""Shared fixtures for api_gateway core service tests."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.api import OperationContext
from secbaas.api.api_gateway import APIKeyRecord


@pytest.fixture
def mock_repository():
    return MagicMock()


@pytest.fixture
def ctx():
    return OperationContext(operator="test_user", env="test")


API_KEY_PREFIX = "xK9mP2nQ"


@pytest.fixture
def sample_record() -> APIKeyRecord:
    now = datetime.now()
    return APIKeyRecord(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        api_key_hash="salt:dk",
        api_key_prefix=API_KEY_PREFIX,
        key_name="test-key",
        app_id="bot-uuid-123",
        app_type="baas",
        description="test",
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test_user",
        tenant="test-tenant",
        env="test",
        creator="test_user",
        modifier=None,
        policy=None,
    )
