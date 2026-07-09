"""Unit tests for DefaultAPIKeyValidator.

Covers:
- verify: success path, invalid format, no active key, hash mismatch
- verify_sync: success path, invalid format, no active key, hash mismatch
"""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.api.api_gateway import APIKeyRecord


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def validator(repo):
    from secbaas.core.service.api_gateway._key_validator import DefaultAPIKeyValidator

    return DefaultAPIKeyValidator(repository=repo)


def _make_record(**overrides) -> APIKeyRecord:
    from datetime import datetime

    defaults = dict(
        id=1,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="dGVzdC1zYWx0:dGVzdC1kaw==",
        api_key_prefix="xK9mP2nQ",
        key_name="test-key",
        app_id="app-1",
        app_type="baas",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="ACTIVE",
        owner="test_user",
        tenant="t1",
        env="test",
        creator="test_user",
        modifier=None,
        policy=None,
    )
    defaults.update(overrides)
    return APIKeyRecord(**defaults)


# ==================== verify ====================


class TestVerify:
    async def test_verify_success(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=True,
        ):
            result = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert result is not None
        assert result.id == 1

    async def test_verify_invalid_format_too_short(self, validator, repo):
        result = await validator.verify("short")
        assert result is None
        repo.get_by_prefix_and_status.assert_not_called()

    async def test_verify_invalid_format_empty(self, validator, repo):
        result = await validator.verify("")
        assert result is None

    async def test_verify_invalid_format_none(self, validator, repo):
        result = await validator.verify(None)  # type: ignore[arg-type]
        assert result is None

    async def test_verify_no_active_key(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = None

        result = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert result is None

    async def test_verify_hash_mismatch(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=False,
        ):
            result = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert result is None


# ==================== verify_sync ====================


class TestVerifySync:
    def test_verify_sync_success(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=True,
        ):
            result = validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        assert result is not None
        assert result.id == 1

    def test_verify_sync_invalid_format(self, validator, repo):
        result = validator.verify_sync("short")
        assert result is None

    def test_verify_sync_no_active_key(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = None

        result = validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        assert result is None

    def test_verify_sync_hash_mismatch(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=False,
        ):
            result = validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        assert result is None
