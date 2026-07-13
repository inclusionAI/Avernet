"""OrmAPIKeyRepository unit tests.

Uses pytest + MagicMock pattern matching the existing
test_zdas_api_gateway_repository.py and test_orm_bot_run_repository.py.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.api.api_gateway import APIKeyRecord
from secbaas.core.repository.api_gateway import OrmAPIKeyRepository

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture
def repository(mock_database):
    return OrmAPIKeyRepository(database=mock_database)


# ==================== Helper ====================


def _mock_record(
    id_val=1,
    gmt_create=None,
    gmt_modified=None,
    api_key_hash="hash_abc123",
    api_key_prefix="sk-abc1",
    key_name="my-key",
    app_id="app-001",
    app_type="web",
    description="A test key",
    rate_limit_rpm=100,
    rate_limit_rpd=10000,
    status="ACTIVE",
    owner="admin",
    tenant="tenant-1",
    env="dev",
    creator="admin",
    modifier=None,
    policy='{"allow": ["read"]}',
):
    """Build a mock APIKeyRecord with default values."""
    now = datetime.now()
    return APIKeyRecord(
        id=id_val,
        gmt_create=gmt_create or now,
        gmt_modified=gmt_modified or now,
        api_key_hash=api_key_hash,
        api_key_prefix=api_key_prefix,
        key_name=key_name,
        app_id=app_id,
        app_type=app_type,
        description=description,
        rate_limit_rpm=rate_limit_rpm,
        rate_limit_rpd=rate_limit_rpd,
        status=status,
        owner=owner,
        tenant=tenant,
        env=env,
        creator=creator,
        modifier=modifier,
        policy=policy,
    )


# ==================== Constructor ====================


class TestConstructor:
    def test_constructor_sets_attributes(self, mock_database):
        repo = OrmAPIKeyRepository(database=mock_database)
        assert repo._database is mock_database


# ==================== insert ====================


class TestInsert:
    def test_insert_returns_lastrowid(self, repository, mock_session):
        def _capture_add(row):
            row.id = 42

        mock_session.add.side_effect = _capture_add

        result = repository.insert(
            api_key_hash="hash_new",
            api_key_prefix="sk-new",
            key_name="New Key",
            app_id="app-new",
            app_type="web",
            description="New key description",
            rate_limit_rpm=60,
            rate_limit_rpd=1000,
            status="ACTIVE",
            owner="owner-1",
            tenant="t1",
            env="dev",
            creator="creator-1",
            policy='{"allow": ["read"]}',
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repository, mock_session):
        def _capture_add(row):
            row.id = 1

        mock_session.add.side_effect = _capture_add

        repository.insert(
            api_key_hash="hash_new",
            api_key_prefix="sk-new",
            key_name="New Key",
            app_id="app-new",
            app_type="web",
            description="New key description",
            rate_limit_rpm=60,
            rate_limit_rpd=1000,
            status="ACTIVE",
            owner="owner-1",
            tenant="t1",
            env="dev",
            creator="creator-1",
            policy='{"allow": ["read"]}',
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.api_key_hash == "hash_new"
        assert added_model.api_key_prefix == "sk-new"
        assert added_model.key_name == "New Key"
        assert added_model.app_id == "app-new"
        assert added_model.app_type == "web"
        assert added_model.description == "New key description"
        assert added_model.rate_limit_rpm == 60
        assert added_model.rate_limit_rpd == 1000
        assert added_model.status == "ACTIVE"
        assert added_model.owner == "owner-1"
        assert added_model.tenant == "t1"
        assert added_model.env == "dev"
        assert added_model.creator == "creator-1"
        assert added_model.modifier == "creator-1"
        assert added_model.policy == '{"allow": ["read"]}'

    def test_insert_with_none_optional_fields(self, repository, mock_session):
        def _capture_add(row):
            row.id = 1

        mock_session.add.side_effect = _capture_add

        repository.insert(
            api_key_hash="hash_min",
            api_key_prefix="sk-min",
            key_name=None,
            app_id="app-min",
            app_type=None,
            description=None,
            rate_limit_rpm=None,
            rate_limit_rpd=None,
            status="PENDING",
            owner="owner-min",
            tenant=None,
            env="dev",
            creator="creator-min",
            policy=None,
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.key_name is None
        assert added_model.app_type is None
        assert added_model.description is None
        assert added_model.rate_limit_rpm is None
        assert added_model.rate_limit_rpd is None
        assert added_model.tenant is None
        assert added_model.policy is None


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repository, mock_session):
        record = _mock_record(id_val=5, api_key_prefix="sk-abc", key_name="My Key")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(5)

        assert result is not None
        assert result.id == 5
        assert result.api_key_prefix == "sk-abc"
        assert result.key_name == "My Key"
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999)

        assert result is None

    def test_filters_by_id(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repository.get_by_id(42)

        mock_session.query.assert_called_once()


# ==================== get_by_prefix ====================


class TestGetByPrefix:
    def test_found(self, repository, mock_session):
        record = _mock_record(id_val=10, api_key_prefix="sk-xyz", key_name="Prefix Key")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_prefix("sk-xyz")

        assert result is not None
        assert result.id == 10
        assert result.api_key_prefix == "sk-xyz"
        assert result.key_name == "Prefix Key"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_prefix("nonexistent")

        assert result is None


# ==================== get_by_prefix_and_status ====================


class TestGetByPrefixAndStatus:
    def test_found(self, repository, mock_session):
        record = _mock_record(id_val=10, api_key_prefix="sk-xyz", status="ACTIVE")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_prefix_and_status("sk-xyz", "ACTIVE")

        assert result is not None
        assert result.id == 10
        assert result.api_key_prefix == "sk-xyz"
        assert result.status == "ACTIVE"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_prefix_and_status("nonexistent", "ACTIVE")

        assert result is None

    def test_filters_by_env_when_provided(self, repository, mock_session):
        """传入 env 时，查询应追加 env 过滤条件。"""
        record = _mock_record(id_val=11, api_key_prefix="sk-env", status="ACTIVE")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        filtered = mock_session.query.return_value.filter.return_value
        filtered.filter.return_value.first.return_value = mock_model

        from secbaas.core.repository.api_gateway._orm_model import APIKeyModel

        result = repository.get_by_prefix_and_status("sk-env", "ACTIVE", env="prod")

        assert result is not None
        # 应有两次 filter 调用：第一次 prefix+status，第二次 env
        first_filter = mock_session.query.return_value.filter
        first_filter.assert_called_once()
        second_filter = first_filter.return_value.filter
        second_filter.assert_called_once()
        # 第二次 filter 的参数应为 env 过滤条件
        env_arg = second_filter.call_args[0][0]
        assert str(env_arg) == str(APIKeyModel.env == "prod")

    def test_no_env_filter_when_env_none(self, repository, mock_session):
        """不传 env 时，不追加额外过滤（向后兼容）。"""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repository.get_by_prefix_and_status("sk-env", "ACTIVE")

        first_filter = mock_session.query.return_value.filter
        # 不传 env → 只有一次 filter（prefix+status），不追加 env 过滤
        first_filter.assert_called_once()
        first_filter.return_value.filter.assert_not_called()


# ==================== list_keys ====================


def _setup_list_keys_mock(mock_session, total, items):
    """Configure mock_session for list_keys to return given total and items.

    Builds model mocks that to_record() returns the given APIKeyRecord items.
    """
    models = []
    for item in items:
        m = MagicMock()
        m.to_record.return_value = item
        models.append(m)

    query_mock = MagicMock()
    query_mock.filter.return_value = query_mock
    query_mock.with_entities.return_value.scalar.return_value = total
    query_mock.order_by.return_value.offset.return_value.limit.return_value.all.return_value = models
    mock_session.query.return_value = query_mock
    return query_mock


class TestListKeys:
    def test_no_filters_returns_all(self, repository, mock_session):
        recs = [_mock_record(id_val=1), _mock_record(id_val=2)]
        _setup_list_keys_mock(mock_session, total=5, items=recs)

        total, items = repository.list_keys()

        assert total == 5
        assert len(items) == 2
        assert items[0].id == 1
        assert items[1].id == 2

    def test_with_filters(self, repository, mock_session):
        rec = _mock_record(id_val=1, app_id="app-foo", status="DISABLED")
        _setup_list_keys_mock(mock_session, total=1, items=[rec])

        total, items = repository.list_keys(
            app_id="app-foo",
            status="DISABLED",
            creator="user1",
            owner="owner1",
            tenant="t1",
            env="dev",
        )

        assert total == 1
        assert len(items) == 1
        assert items[0].app_id == "app-foo"

    def test_empty_list(self, repository, mock_session):
        _setup_list_keys_mock(mock_session, total=0, items=[])

        total, items = repository.list_keys()

        assert total == 0
        assert items == []

    def test_with_pagination(self, repository, mock_session):
        recs = [_mock_record(id_val=i) for i in range(1, 6)]
        _setup_list_keys_mock(mock_session, total=50, items=recs)

        total, items = repository.list_keys(page=2, page_size=5)

        assert total == 50
        assert len(items) == 5


# ==================== update ====================


class TestUpdate:
    def test_update_single_field(self, repository, mock_session):
        repository.update(key_id=1, key_name="Updated Name")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["key_name"] == "Updated Name"
        assert "gmt_modified" in update_dict

    def test_update_multiple_fields(self, repository, mock_session):
        repository.update(
            key_id=1,
            key_name="New Name",
            description="New Desc",
            app_type="rpc",
            rate_limit_rpm=200,
            rate_limit_rpd=20000,
            owner="new-owner",
            tenant="new-tenant",
            modifier="new-mod",
            policy='{"allow": ["write"]}',
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["key_name"] == "New Name"
        assert update_dict["description"] == "New Desc"
        assert update_dict["app_type"] == "rpc"
        assert update_dict["rate_limit_rpm"] == 200
        assert update_dict["rate_limit_rpd"] == 20000
        assert update_dict["owner"] == "new-owner"
        assert update_dict["tenant"] == "new-tenant"
        assert update_dict["modifier"] == "new-mod"
        assert update_dict["policy"] == '{"allow": ["write"]}'
        assert "gmt_modified" in update_dict

    def test_update_with_app_id(self, repository, mock_session):
        repository.update(key_id=5, app_id="app-xyz")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["app_id"] == "app-xyz"

    def test_update_no_fields_returns_early(self, repository, mock_session):
        repository.update(key_id=1)
        mock_session.query.assert_not_called()

    def test_update_only_key_name(self, repository, mock_session):
        repository.update(key_id=5, key_name="Name Only")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["key_name"] == "Name Only"
        assert "description" not in update_dict
        assert "app_type" not in update_dict

    def test_update_only_rate_limit_rpm(self, repository, mock_session):
        repository.update(key_id=5, rate_limit_rpm=300)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["rate_limit_rpm"] == 300

    def test_update_only_rate_limit_rpd(self, repository, mock_session):
        repository.update(key_id=5, rate_limit_rpd=30000)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["rate_limit_rpd"] == 30000

    def test_update_only_owner(self, repository, mock_session):
        repository.update(key_id=5, owner="new-owner")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["owner"] == "new-owner"

    def test_update_only_tenant(self, repository, mock_session):
        repository.update(key_id=5, tenant="new-tenant")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["tenant"] == "new-tenant"

    def test_update_only_modifier(self, repository, mock_session):
        repository.update(key_id=5, modifier="new-mod")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "new-mod"

    def test_update_only_policy(self, repository, mock_session):
        repository.update(key_id=5, policy='{"k":"v"}')

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["policy"] == '{"k":"v"}'

    def test_update_accepts_zero_rate_limits(self, repository, mock_session):
        repository.update(key_id=5, rate_limit_rpm=0, rate_limit_rpd=0)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["rate_limit_rpm"] == 0
        assert update_dict["rate_limit_rpd"] == 0


# ==================== update_status ====================


class TestUpdateStatus:
    def test_update_status_with_modifier(self, repository, mock_session):
        repository.update_status(key_id=10, status="DISABLED", modifier="admin")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "DISABLED"
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_status_without_modifier(self, repository, mock_session):
        repository.update_status(key_id=10, status="ACTIVE")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "ACTIVE"
        assert "modifier" not in update_dict

    def test_update_status_with_none_modifier(self, repository, mock_session):
        repository.update_status(key_id=10, status="REVOKED", modifier=None)

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "REVOKED"
        assert "modifier" not in update_dict


# ==================== exists_prefix ====================


class TestExistsPrefix:
    def test_prefix_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1

        result = repository.exists_prefix("sk-abc")

        assert result is True

    def test_prefix_does_not_exist(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repository.exists_prefix("sk-nonexistent")

        assert result is False

    def test_prefix_count_greater_than_one(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 5

        result = repository.exists_prefix("sk-multi")

        assert result is True
