"""Unit tests for DefaultAPIKeyValidator.

Covers:
- verify: success path, invalid format, no active key, hash mismatch
- verify_sync: success path, invalid format, no active key, hash mismatch
"""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.api_gateway import APIKeyRecord


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def validator(repo):
    from secbaas.community.core.service.api_gateway._key_validator import (
        DefaultAPIKeyValidator,
    )

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
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
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
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=False,
        ):
            result = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert result is None

    async def test_verify_passes_current_env_to_repository(self, validator, repo):
        """verify 必须把当前环境透传给 repository，钉死当前 env 过滤。"""
        repo.get_by_prefix_and_status.return_value = _make_record()

        with (
            patch(
                "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
                return_value=True,
            ),
            patch(
                "secbaas.community.core.service.api_gateway._key_validator.env_utils.get_current_env",
                return_value="prod",
            ),
        ):
            await validator.verify("xK9mP2nQ1234567890123456789012345")

        args, kwargs = repo.get_by_prefix_and_status.call_args
        # env 通过关键字传入
        assert kwargs.get("env") == "prod"

    async def test_verify_cross_env_returns_none(self, validator, repo):
        """跨环境的 key（repository 按 env 过滤后查不到）→ None，统一 401 不泄露。"""
        # repository 在当前 env 下查不到（跨环境记录被过滤）→ 返回 None
        repo.get_by_prefix_and_status.return_value = None

        with patch(
            "secbaas.community.core.service.api_gateway._key_validator.env_utils.get_current_env",
            return_value="prod",
        ):
            result = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert result is None


# ==================== verify_sync ====================


class TestVerifySync:
    def test_verify_sync_success(self, validator, repo):
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
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
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=False,
        ):
            result = validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        assert result is None

    def test_verify_sync_passes_current_env_to_repository(self, validator, repo):
        """verify_sync 必须把当前环境透传给 repository。"""
        repo.get_by_prefix_and_status.return_value = _make_record()

        with (
            patch(
                "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
                return_value=True,
            ),
            patch(
                "secbaas.community.core.service.api_gateway._key_validator.env_utils.get_current_env",
                return_value="prod",
            ),
        ):
            validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        args, kwargs = repo.get_by_prefix_and_status.call_args
        assert kwargs.get("env") == "prod"


# ==================== 缓存行为 ====================


class TestVerifyCache:
    async def test_second_verify_hits_cache(self, validator, repo):
        """同一 key 二次验证命中缓存：不再查库、不再跑 PBKDF2。"""
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=True,
        ) as mock_verify:
            first = await validator.verify("xK9mP2nQ1234567890123456789012345")
            second = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert first is second
        assert repo.get_by_prefix_and_status.call_count == 1
        assert mock_verify.call_count == 1

    async def test_negative_result_cached(self, validator, repo):
        """查不到记录的失败结果也走缓存：二次验证不再查库。"""
        repo.get_by_prefix_and_status.return_value = None

        assert await validator.verify("xK9mP2nQ1234567890123456789012345") is None
        assert await validator.verify("xK9mP2nQ1234567890123456789012345") is None

        assert repo.get_by_prefix_and_status.call_count == 1

    async def test_verify_sync_shares_cache_with_verify(self, validator, repo):
        """verify 与 verify_sync 共享同一份缓存。"""
        repo.get_by_prefix_and_status.return_value = _make_record()

        with patch(
            "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
            return_value=True,
        ):
            first = await validator.verify("xK9mP2nQ1234567890123456789012345")
            second = validator.verify_sync("xK9mP2nQ1234567890123456789012345")

        assert first is second
        assert repo.get_by_prefix_and_status.call_count == 1

    async def test_expired_entry_refetches_from_repository(self, validator, repo):
        """缓存过期后重新查库，不返回陈旧结果。"""
        repo.get_by_prefix_and_status.return_value = _make_record()

        with (
            patch(
                "secbaas.community.core.service.api_gateway._key_gen.APIKeyGenerator.verify_key",
                return_value=True,
            ),
            patch(
                "secbaas.community.core.service.api_gateway._key_validator.time"
            ) as fake_time,
        ):
            # 第一次 put 时 monotonic()=0（expires=60），
            # 二次 get 时 =100 已过期 → miss → 重新查库后再 put
            fake_time.monotonic.side_effect = [0.0, 100.0, 100.0]
            first = await validator.verify("xK9mP2nQ1234567890123456789012345")
            second = await validator.verify("xK9mP2nQ1234567890123456789012345")

        assert first is not None
        assert second is not None
        assert repo.get_by_prefix_and_status.call_count == 2


class TestTTLCache:
    def test_put_evicts_oldest_beyond_max_entries(self):
        """超过容量上限时淘汰最旧条目。"""
        from secbaas.community.core.service.api_gateway._key_validator import (
            _MISS,
            _TTLCache,
        )

        cache = _TTLCache(max_entries=2)
        cache.put("a", 1, ttl=60)
        cache.put("b", 2, ttl=60)
        cache.put("c", 3, ttl=60)

        assert cache.get("a") is _MISS
        assert cache.get("b") == 2
        assert cache.get("c") == 3
