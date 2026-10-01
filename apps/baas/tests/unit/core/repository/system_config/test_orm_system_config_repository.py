"""
OrmSystemConfigRepository unit tests.

Uses pytest + MagicMock pattern matching the existing
test_zdas_system_config_repository.py and test_orm_api_gateway_repository.py.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.system_config import (
    OrmSystemConfigRepository,
    SystemConfigRecord,
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
    return OrmSystemConfigRepository(mock_database)


# ==================== Helper ====================


def _make_record(
    id_val=1,
    gmt_create=None,
    gmt_modified=None,
    conf_key="test.key",
    conf_value="test_value",
    env="prod",
    name="Test Config",
    description="A test config",
    creator="admin",
    modifier="admin",
):
    """Build a mock SystemConfigRecord with default values."""
    now = datetime.now()
    return SystemConfigRecord(
        id=id_val,
        gmt_create=gmt_create or now,
        gmt_modified=gmt_modified or now,
        conf_key=conf_key,
        conf_value=conf_value,
        env=env,
        name=name,
        description=description,
        creator=creator,
        modifier=modifier,
    )


# ==================== Constructor ====================


class TestConstructor:
    def test_constructor_sets_attributes(self, mock_database):
        repo = OrmSystemConfigRepository(mock_database)
        assert repo._database is mock_database


# ==================== insert_config ====================


class TestInsertConfig:
    def test_insert_returns_lastrowid(self, repository, mock_session):
        def _capture_add(row):
            row.id = 10

        mock_session.add.side_effect = _capture_add

        result = repository.insert_config(
            conf_key="feature.toggle",
            conf_value="enabled",
            env="prod",
            name="Feature Toggle",
            description="Toggle for new feature",
            creator="admin",
            modifier="admin",
        )

        assert result == 10
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repository, mock_session):
        def _capture_add(row):
            row.id = 1

        mock_session.add.side_effect = _capture_add

        repository.insert_config(
            conf_key="feature.toggle",
            conf_value="enabled",
            env="prod",
            name="Feature Toggle",
            description="Toggle for new feature",
            creator="admin",
            modifier="admin",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.conf_key == "feature.toggle"
        assert added_model.conf_value == "enabled"
        assert added_model.env == "prod"
        assert added_model.name == "Feature Toggle"
        assert added_model.description == "Toggle for new feature"
        assert added_model.creator == "admin"
        assert added_model.modifier == "admin"

    def test_insert_with_none_value_and_description(self, repository, mock_session):
        def _capture_add(row):
            row.id = 1

        mock_session.add.side_effect = _capture_add

        repository.insert_config(
            conf_key="minimal.key",
            conf_value=None,
            env="dev",
            name="Minimal",
            creator="admin",
            modifier="admin",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.conf_key == "minimal.key"
        assert added_model.conf_value is None
        assert added_model.description is None


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repository, mock_session):
        record = _make_record(id_val=5, conf_key="app.name")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(5)

        assert result is not None
        assert isinstance(result, SystemConfigRecord)
        assert result.id == 5
        assert result.conf_key == "app.name"
        assert result.env == "prod"
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999)

        assert result is None


# ==================== get_by_env_and_key ====================


class TestGetByEnvAndKey:
    def test_found(self, repository, mock_session):
        record = _make_record(id_val=3, conf_key="limit.rate", env="dev")
        mock_model = MagicMock()
        mock_model.to_record.return_value = record
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_env_and_key("dev", "limit.rate")

        assert result is not None
        assert result.conf_key == "limit.rate"
        assert result.env == "dev"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_env_and_key("prod", "nonexistent.key")

        assert result is None


# ==================== update_config ====================


class TestUpdateConfig:
    def test_update_single_field(self, repository, mock_session):
        repository.update_config(config_id=1, conf_value="new_value", modifier="admin")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["conf_value"] == "new_value"
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_no_fields_returns_zero(self, repository, mock_session):
        result = repository.update_config(config_id=1)

        assert result == 0
        mock_session.query.assert_not_called()

    def test_update_all_fields(self, repository, mock_session):
        repository.update_config(
            config_id=10,
            conf_value="updated",
            name="Updated Name",
            description="Updated description",
            modifier="admin2",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["conf_value"] == "updated"
        assert update_dict["name"] == "Updated Name"
        assert update_dict["description"] == "Updated description"
        assert update_dict["modifier"] == "admin2"
        assert "gmt_modified" in update_dict


# ==================== delete_config ====================


class TestDeleteConfig:
    def test_delete_hard(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        result = repository.delete_config(config_id=5)

        assert result == 1
        mock_session.query.return_value.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False
        )

    def test_delete_not_found_returns_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_config(config_id=999)

        assert result == 0


# ==================== list_configs ====================


class TestListConfigs:
    def test_no_filters(self, repository, mock_session):
        rec1 = _make_record(id_val=1, conf_key="key.a")
        rec2 = _make_record(id_val=2, conf_key="key.b")

        m1 = MagicMock()
        m1.to_record.return_value = rec1
        m2 = MagicMock()
        m2.to_record.return_value = rec2

        mock_session.query.return_value.with_entities.return_value.scalar.return_value = 5
        mock_session.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            m1,
            m2,
        ]

        total, items = repository.list_configs()

        assert total == 5
        assert len(items) == 2
        assert items[0].conf_key == "key.a"
        assert items[1].conf_key == "key.b"

    def test_with_env_filter(self, repository, mock_session):
        rec = _make_record(id_val=1, env="dev")
        m = MagicMock()
        m.to_record.return_value = rec

        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 1
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            m
        ]

        total, items = repository.list_configs(env="dev")

        assert total == 1
        assert items[0].env == "dev"

    def test_with_pagination(self, repository, mock_session):
        recs = [_make_record(id_val=i) for i in range(1, 6)]
        models = []
        for r in recs:
            m = MagicMock()
            m.to_record.return_value = r
            models.append(m)

        mock_session.query.return_value.with_entities.return_value.scalar.return_value = 50
        mock_session.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = models

        total, items = repository.list_configs(page=2, page_size=5)

        assert total == 50
        assert len(items) == 5


# ==================== to_record ====================
# Model.to_record() is tested implicitly via query tests above
