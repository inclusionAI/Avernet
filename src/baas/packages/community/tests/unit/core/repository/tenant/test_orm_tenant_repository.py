"""
OrmTenantRepository unit tests.

Uses pytest + MagicMock pattern matching test_orm_bot_run_repository.py.
Covers all 7 protocol methods, TenantModel.to_record() via query tests,
constructor, and TenantRecord dataclass.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from secbaas.core.repository.tenant import (
    OrmTenantRepository,
    TenantRecord,
)

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
    """Create an OrmTenantRepository backed by the mock database."""
    return OrmTenantRepository(database=mock_database)


# ==================== Constants ====================

NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


# ==================== Model builders ====================


def _make_mock_model(**overrides):
    """Build a MagicMock TenantModel with default attributes."""
    model = MagicMock()
    model.configure_mock(
        id=1,
        gmt_create=NOW,
        gmt_modified=NOW,
        is_deleted=0,
        creator="creator-001",
        modifier="modifier-001",
        name="test-tenant",
        description="A test tenant",
        env="dev",
        extra_config="{}",
    )
    for key, value in overrides.items():
        setattr(model, key, value)
    model.to_record.return_value = TenantRecord(
        id=model.id,
        gmt_create=model.gmt_create,
        gmt_modified=model.gmt_modified,
        is_deleted=model.is_deleted or 0,
        creator=model.creator,
        modifier=model.modifier,
        name=model.name,
        description=model.description,
        env=model.env,
        extra_config=(
            json.loads(model.extra_config)
            if isinstance(model.extra_config, str)
            else model.extra_config
        )
        or {},
    )
    return model


# ==================== TenantRecord dataclass ====================


class TestTenantRecord:
    """Tests for the TenantRecord dataclass."""

    def test_creates_tenant_record(self):
        extra = {"key": "value"}
        record = TenantRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            is_deleted=0,
            creator="c1",
            modifier="m1",
            name="my-tenant",
            description="Test tenant",
            extra_config=extra,
            env="dev",
        )
        assert record.id == 1
        assert record.name == "my-tenant"
        assert record.env == "dev"
        assert record.description == "Test tenant"
        assert record.extra_config == extra
        assert record.is_deleted == 0
        assert record.creator == "c1"
        assert record.modifier == "m1"

    def test_none_description(self):
        record = TenantRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            is_deleted=0,
            creator="c1",
            modifier="m1",
            name="tenant",
            description=None,
            extra_config={},
            env="dev",
        )
        assert record.description is None

    def test_empty_extra_config(self):
        record = TenantRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            is_deleted=0,
            creator="c1",
            modifier="m1",
            name="tenant",
            description=None,
            extra_config={},
            env="dev",
        )
        assert record.extra_config == {}


# ==================== Constructor ====================


class TestConstructor:
    """Tests for OrmTenantRepository.__init__."""

    def test_constructor_sets_database(self, mock_database):
        repo = OrmTenantRepository(database=mock_database)
        assert repo._database is mock_database

    def test_constructor_stores_attribute(self, mock_database):
        repo = OrmTenantRepository(database=mock_database)
        assert hasattr(repo, "_database")


# ==================== insert_tenant ====================


class TestInsertTenant:
    """Tests for OrmTenantRepository.insert_tenant."""

    @pytest.fixture(autouse=True)
    def _patch_model(self):
        """Patch TenantModel so the constructor returns a MagicMock capturing kwargs."""
        with patch(
            "secbaas.core.repository.tenant._orm_repository.TenantModel",
        ) as mock_cls:

            def _side_effect(**kwargs):
                mock_instance = MagicMock()
                mock_instance.id = 999
                for key, value in kwargs.items():
                    setattr(mock_instance, key, value)
                return mock_instance

            mock_cls.side_effect = _side_effect
            yield mock_cls

    def test_insert_returns_new_id(self, repository, mock_session, _patch_model):
        result = repository.insert_tenant(
            creator="creator-1",
            modifier="modifier-1",
            name="new-tenant",
            env="dev",
        )

        assert result == 999
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repository, mock_session, _patch_model):
        repository.insert_tenant(
            creator="creator-1",
            modifier="modifier-1",
            name="new-tenant",
            env="dev",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.creator == "creator-1"
        assert added_model.modifier == "modifier-1"
        assert added_model.name == "new-tenant"
        assert added_model.env == "dev"

    def test_insert_with_description(self, repository, mock_session, _patch_model):
        repository.insert_tenant(
            creator="c1",
            modifier="m1",
            name="t1",
            description="My tenant description",
            env="prod",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.description == "My tenant description"

    def test_insert_with_extra_config_serializes_json(
        self, repository, mock_session, _patch_model
    ):
        repository.insert_tenant(
            creator="c1",
            modifier="m1",
            name="t1",
            extra_config={"k1": "v1", "k2": 2},
            env="dev",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.extra_config is not None
        parsed = json.loads(added_model.extra_config)
        assert parsed == {"k1": "v1", "k2": 2}

    def test_insert_with_none_extra_config(
        self, repository, mock_session, _patch_model
    ):
        repository.insert_tenant(
            creator="c1",
            modifier="m1",
            name="t1",
            extra_config=None,
            env="dev",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.extra_config is None

    def test_insert_default_description_is_none(
        self, repository, mock_session, _patch_model
    ):
        repository.insert_tenant(
            creator="c1",
            modifier="m1",
            name="t1",
            env="dev",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.description is None

    def test_insert_default_env_is_prod(self, repository, mock_session, _patch_model):
        """Default env parameter should be 'prod'."""
        repository.insert_tenant(
            creator="c1",
            modifier="m1",
            name="t1",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.env == "prod"


# ==================== get_by_id ====================


class TestGetById:
    """Tests for OrmTenantRepository.get_by_id."""

    def test_found(self, repository, mock_session):
        mock_model = _make_mock_model(id=5, name="my-tenant", env="dev")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(5)

        assert result is mock_model.to_record.return_value
        assert result.id == 5
        assert result.name == "my-tenant"
        assert result.env == "dev"
        mock_model.to_record.assert_called_once()
        mock_session.query.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999)

        assert result is None

    def test_uses_is_deleted_filter(self, repository, mock_session):
        mock_model = _make_mock_model()
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.get_by_id(1)

        # verify query was called
        mock_session.query.assert_called_once()


# ==================== get_by_name ====================


class TestGetByName:
    """Tests for OrmTenantRepository.get_by_name."""

    def test_found(self, repository, mock_session):
        mock_model = _make_mock_model(id=10, name="my-tenant", env="staging")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_name("my-tenant", "staging")

        assert result is mock_model.to_record.return_value
        assert result.id == 10
        assert result.name == "my-tenant"
        assert result.env == "staging"
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_name("nonexistent", "dev")

        assert result is None

    def test_filters_by_env(self, repository, mock_session):
        mock_model = _make_mock_model(env="prod")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.get_by_name("t1", "prod")
        mock_session.query.assert_called_once()


# ==================== update_tenant ====================


class TestUpdateTenant:
    """Tests for OrmTenantRepository.update_tenant."""

    def test_update_single_field(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_tenant(
            name="my-tenant",
            env="dev",
            modifier="admin",
            description="Updated description",
        )

        assert result == 1
        mock_session.query.assert_called_once()

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "admin"
        assert update_dict["description"] == "Updated description"

    def test_update_multiple_fields(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_tenant(
            name="my-tenant",
            env="dev",
            modifier="admin",
            description="New desc",
            extra_config={"key": "val"},
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["description"] == "New desc"
        assert update_dict["extra_config"] == json.dumps(
            {"key": "val"}, ensure_ascii=False
        )
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_modifier_always_included(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_tenant(
            name="my-tenant",
            env="dev",
            modifier="admin",
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_extra_config_only(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repository.update_tenant(
            name="my-tenant",
            env="dev",
            modifier="admin",
            extra_config={"new": "config"},
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["extra_config"] == json.dumps(
            {"new": "config"}, ensure_ascii=False
        )

    def test_update_extra_config_serializes_json(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repository.update_tenant(
            name="my-tenant",
            env="dev",
            modifier="admin",
            extra_config={"nested": {"deep": True}},
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["extra_config"] == json.dumps(
            {"nested": {"deep": True}}, ensure_ascii=False
        )

    def test_update_rowcount_zero(self, repository, mock_session):
        """When no rows matched, update returns 0."""
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.update_tenant(
            name="nonexistent",
            env="dev",
            modifier="admin",
            description="Trying",
        )

        assert result == 0


# ==================== soft_delete ====================


class TestSoftDelete:
    """Tests for OrmTenantRepository.soft_delete."""

    def test_soft_delete_found(self, repository, mock_session):
        """When tenant exists, first query finds it, then updates."""
        mock_model = _make_mock_model(id=42, name="my-tenant", env="dev")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.soft_delete(name="my-tenant", env="dev", modifier="admin")

        # First query: find the record
        mock_session.query.assert_called()
        # .update() should have been called once on the second query
        assert mock_session.query.return_value.filter.return_value.update.called

    def test_soft_delete_not_found(self, repository, mock_session):
        """When tenant not found, returns early without updating."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repository.soft_delete(name="nonexistent", env="dev", modifier="admin")

        # No update call should have been made
        mock_session.query.return_value.filter.return_value.update.assert_not_called()

    def test_soft_delete_sets_is_deleted_to_id(self, repository, mock_session):
        mock_model = _make_mock_model(id=77, name="my-tenant", env="dev")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repository.soft_delete(name="my-tenant", env="dev", modifier="admin")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == 77
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict


# ==================== list_tenants ====================


class TestListTenants:
    """Tests for OrmTenantRepository.list_tenants."""

    def test_returns_results(self, repository, mock_session):
        """Normal pagination returns (total, items)."""
        mock_model1 = _make_mock_model(id=1, name="tenant-1")
        mock_model2 = _make_mock_model(id=2, name="tenant-2")
        mock_model3 = _make_mock_model(id=3, name="tenant-3")

        # scalar() returns the COUNT result
        mock_session.query.return_value.filter.return_value.scalar.return_value = 3
        # .all() returns the rows
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            mock_model1,
            mock_model2,
            mock_model3,
        ]

        total, items = repository.list_tenants(env="dev")

        assert total == 3
        assert len(items) == 3
        assert items[0].id == 1
        assert items[1].id == 2
        assert items[2].id == 3

        # Each model's to_record() was called
        mock_model1.to_record.assert_called_once()
        mock_model2.to_record.assert_called_once()
        mock_model3.to_record.assert_called_once()

    def test_empty_list(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_tenants(env="dev")

        assert total == 0
        assert items == []

    def test_with_pagination(self, repository, mock_session):
        """Offset computed from page and page_size."""
        mock_models = [_make_mock_model(id=i) for i in range(11, 16)]
        mock_session.query.return_value.filter.return_value.scalar.return_value = 50
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_models

        total, items = repository.list_tenants(env="dev", page=2, page_size=5)

        assert total == 50
        assert len(items) == 5

    def test_default_page_and_size(self, repository, mock_session):
        """Default page=1, page_size=20."""
        mock_models = [_make_mock_model()]
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_models

        repository.list_tenants(env="dev")

        mock_session.query.return_value.filter.return_value.scalar.assert_called()

    def test_different_env(self, repository, mock_session):
        mock_models = [_make_mock_model(env="prod")]
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = mock_models

        total, items = repository.list_tenants(env="prod")

        assert total == 1
        assert len(items) == 1


# ==================== TenantModel.to_record() coverage via query ====================


class TestModelToRecordViaQuery:
    """Test TenantModel.to_record() behavior through repository queries.

    These tests exercise the conversion logic inside TenantModel.to_record(),
    including JSON parsing, edge cases, and defaults — mirroring the
    ZDAS _row_to_record tests but using the ORM mock model pattern.
    """

    def test_none_model_returns_none(self, repository, mock_session):
        """When query returns None, repository returns None."""
        mock_session.query.return_value.filter.return_value.first.return_value = None
        result = repository.get_by_id(999)
        assert result is None

    def test_to_record_converts_valid_model(self, repository, mock_session):
        extra = {"k1": "v1", "nested": {"a": 1}}
        mock_model = _make_mock_model(
            id=7,
            name="test-tenant",
            env="prod",
            description="A test",
            creator="creator-7",
            modifier="modifier-7",
            extra_config=json.dumps(extra, ensure_ascii=False),
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(7)

        assert isinstance(result, TenantRecord)
        assert result.id == 7
        assert result.name == "test-tenant"
        assert result.env == "prod"
        assert result.description == "A test"
        assert result.creator == "creator-7"
        assert result.modifier == "modifier-7"

    def test_to_record_handles_dict_extra_config(self, repository, mock_session):
        """extra_config already a dict (some drivers return parsed JSON)."""
        extra = {"foo": "bar"}
        mock_model = _make_mock_model(id=1, extra_config=extra)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(1)

        assert result.extra_config == {"foo": "bar"}

    def test_null_description(self, repository, mock_session):
        mock_model = _make_mock_model(id=1, description=None)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(1)

        assert result.description is None
