"""Unit tests for DefaultAPIKeyService.

Covers:
- create_key: success, prefix collision retry, max retries exhausted
- get_key: found, not found
- list_keys: paginated results
- update_key: success, not found, env mismatch
- activate: success, not found, wrong status, env mismatch
- deactivate: success, wrong status
- revoke: success, already revoked, not found
"""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api import OperationContext
from secbaas.community.api.api_gateway import (
    APIKeyCreate,
    APIKeyQuery,
    APIKeyRecord,
    APIKeyUpdate,
)


@pytest.fixture
def repo():
    return MagicMock()


@pytest.fixture
def ctx():
    return OperationContext(operator="test_user", env="test")


@pytest.fixture
def service(repo):
    from secbaas.community.core.service.api_gateway._key_service import (
        DefaultAPIKeyService,
    )

    return DefaultAPIKeyService(repository=repo)


# ==================== create_key ====================


class TestCreateKey:
    async def test_create_key_success(self, service, repo, ctx):
        repo.exists_prefix.return_value = False
        repo.insert.return_value = 1
        repo.get_by_id.return_value = _make_record(id=1)

        data = APIKeyCreate(app_id="app-1", tenant="t1")
        result = await service.create_key(data, ctx)

        assert result.api_key is not None
        assert len(result.api_key) == 32
        assert result.id == 1
        repo.insert.assert_called_once()

    async def test_create_key_prefix_collision_retry(self, service, repo, ctx):
        repo.exists_prefix.side_effect = [True, False]
        repo.insert.return_value = 1
        repo.get_by_id.return_value = _make_record(id=1)

        data = APIKeyCreate(app_id="app-1", tenant="t1")
        result = await service.create_key(data, ctx)

        assert result.api_key is not None
        assert repo.exists_prefix.call_count == 2

    async def test_create_key_max_retries_exhausted(self, service, repo, ctx):
        repo.exists_prefix.return_value = True

        data = APIKeyCreate(app_id="app-1", tenant="t1")
        with pytest.raises(Exception) as exc:
            await service.create_key(data, ctx)
        assert "无法生成唯一的 API Key 前缀" in str(exc.value)


# ==================== get_key ====================


class TestGetKey:
    async def test_get_key_found(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1)

        result = await service.get_key(1, ctx)

        assert result is not None
        assert result.id == 1

    async def test_get_key_not_found(self, service, repo, ctx):
        repo.get_by_id.return_value = None

        result = await service.get_key(999, ctx)

        assert result is None


# ==================== list_keys ====================


class TestListKeys:
    async def test_list_keys(self, service, repo, ctx):
        repo.list_keys.return_value = (1, [_make_record(id=1)])

        query = APIKeyQuery(app_id="app-1")
        result = await service.list_keys(query, ctx)

        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].id == 1

    async def test_list_keys_with_app_type(self, service, repo, ctx):
        repo.list_keys.return_value = (2, [_make_record(id=1), _make_record(id=2)])

        query = APIKeyQuery(app_type="bot")
        result = await service.list_keys(query, ctx)

        assert result.total == 2
        assert len(result.items) == 2
        repo.list_keys.assert_called_once_with(
            app_id=None,
            app_type="bot",
            status=None,
            creator=None,
            owner=None,
            tenant=None,
            env="test",
            page=1,
            page_size=20,
        )

    async def test_list_keys_with_owner(self, service, repo, ctx):
        repo.list_keys.return_value = (1, [_make_record(id=1)])

        query = APIKeyQuery(owner="user-001")
        result = await service.list_keys(query, ctx)

        assert result.total == 1
        repo.list_keys.assert_called_once_with(
            app_id=None,
            app_type=None,
            status=None,
            creator=None,
            owner="user-001",
            tenant=None,
            env="test",
            page=1,
            page_size=20,
        )

    async def test_list_keys_with_app_type_and_owner(self, service, repo, ctx):
        """Test the query pattern used by list_my_bot_keys endpoint."""
        repo.list_keys.return_value = (1, [_make_record(id=1)])

        query = APIKeyQuery(app_type="bot", owner="test_user")
        result = await service.list_keys(query, ctx)

        assert result.total == 1
        repo.list_keys.assert_called_once_with(
            app_id=None,
            app_type="bot",
            status=None,
            creator=None,
            owner="test_user",
            tenant=None,
            env="test",
            page=1,
            page_size=20,
        )


# ==================== update_key ====================


class TestUpdateKey:
    async def test_update_key_success(self, service, repo, ctx):
        def get_by_id_side_effect(key_id):
            return _make_record(id=key_id, env="test")

        repo.get_by_id.side_effect = get_by_id_side_effect

        data = APIKeyUpdate(key_name="updated")
        result = await service.update_key(1, data, ctx)

        assert result is not None
        assert result.id == 1
        repo.update.assert_called_once()

    async def test_update_key_not_found(self, service, repo, ctx):
        repo.get_by_id.return_value = None

        data = APIKeyUpdate(key_name="updated")
        result = await service.update_key(999, data, ctx)

        assert result is None

    async def test_update_key_env_mismatch(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="prod")

        data = APIKeyUpdate(key_name="updated")
        with pytest.raises(Exception):
            await service.update_key(1, data, ctx)


# ==================== activate ====================


class TestActivate:
    async def test_activate_success(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="INACTIVE")

        result = await service.activate(1, ctx)

        assert result is not None
        repo.update_status.assert_called_once_with(1, "ACTIVE", "test_user")

    async def test_activate_not_found(self, service, repo, ctx):
        repo.get_by_id.return_value = None

        result = await service.activate(999, ctx)

        assert result is None

    async def test_activate_wrong_status(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="ACTIVE")

        with pytest.raises(Exception):
            await service.activate(1, ctx)

    async def test_activate_env_mismatch(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="prod", status="INACTIVE")

        with pytest.raises(Exception):
            await service.activate(1, ctx)


# ==================== deactivate ====================


class TestDeactivate:
    async def test_deactivate_success(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="ACTIVE")

        result = await service.deactivate(1, ctx)

        assert result is not None
        repo.update_status.assert_called_once_with(1, "INACTIVE", "test_user")

    async def test_deactivate_wrong_status(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="INACTIVE")

        with pytest.raises(Exception):
            await service.deactivate(1, ctx)


# ==================== revoke ====================


class TestRevoke:
    async def test_revoke_success(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="ACTIVE")

        result = await service.revoke(1, ctx)

        assert result is not None
        repo.update_status.assert_called_once_with(1, "REVOKED", "test_user")

    async def test_revoke_already_revoked(self, service, repo, ctx):
        repo.get_by_id.return_value = _make_record(id=1, env="test", status="REVOKED")

        with pytest.raises(Exception):
            await service.revoke(1, ctx)

    async def test_revoke_not_found(self, service, repo, ctx):
        repo.get_by_id.return_value = None

        result = await service.revoke(999, ctx)

        assert result is None


# ==================== helper ====================


def _make_record(id=1, env="test", status="ACTIVE"):
    from datetime import datetime

    return APIKeyRecord(
        id=id,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        api_key_hash="salt:dk",
        api_key_prefix="xK9mP2nQ",
        key_name="test-key",
        app_id="app-1",
        app_type="baas",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status=status,
        owner="test_user",
        tenant="t1",
        env=env,
        creator="test_user",
        modifier=None,
        policy=None,
    )
