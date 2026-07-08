"""
OrmBotDeviceRelRepository unit tests.

Uses pytest + MagicMock pattern matching test_orm_bot_run_repository.py.
Covers all 8 protocol methods, to_record on model.
"""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.core.repository.bot_device_rel import (
    BotDeviceRelRecord,
    OrmBotDeviceRelRepository,
)
from secbaas.core.repository.bot_device_rel._orm_model import BotDeviceRelModel

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
    return OrmBotDeviceRelRepository(database=mock_database)


# ==================== insert_rel ====================


class TestInsertRel:
    def test_insert_returns_id(self, repository, mock_session):
        # Simulate SQLAlchemy autoincrement: the model gets id=42 after flush
        mock_model = MagicMock()
        mock_model.id = 42
        mock_session.add.return_value = None

        # We need to capture the added model and give it an id for the return
        def _add_side_effect(model):
            model.id = 42

        mock_session.add.side_effect = _add_side_effect

        result = repository.insert_rel(
            bot_id=100,
            device_uuid="device-uuid-001",
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert isinstance(added_model, BotDeviceRelModel)
        assert added_model.bot_id == 100
        assert added_model.device_uuid == "device-uuid-001"
        assert added_model.tenant == "test_tenant"
        assert added_model.env == "prod"
        assert added_model.domain == "default"
        assert added_model.creator == "admin"
        assert added_model.modifier == "admin"


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repository, mock_session):
        mock_record = MagicMock(spec=BotDeviceRelRecord)
        mock_record.id = 5
        mock_record.bot_id = 200
        mock_record.device_uuid = "dev-abc"
        mock_record.tenant = "test_tenant"
        mock_record.env = "prod"

        mock_model = MagicMock()
        mock_model.to_record.return_value = mock_record

        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repository.get_by_id(5, "test_tenant", "prod")

        assert result is not None
        assert isinstance(result, BotDeviceRelRecord)
        assert result.id == 5
        assert result.bot_id == 200
        assert result.device_uuid == "dev-abc"
        assert result.tenant == "test_tenant"
        assert result.env == "prod"
        mock_session.query.assert_called_once_with(BotDeviceRelModel)
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999, "test_tenant", "prod")

        assert result is None


# ==================== list_by_bot_id ====================


class TestListByBotId:
    def test_returns_multiple_records(self, repository, mock_session):
        def _make_record(id_val, device_uuid):
            record = MagicMock(spec=BotDeviceRelRecord)
            record.id = id_val
            record.device_uuid = device_uuid
            return record

        records = [
            _make_record(1, "dev-001"),
            _make_record(2, "dev-002"),
            _make_record(3, "dev-003"),
        ]

        models = []
        for r in records:
            m = MagicMock()
            m.to_record.return_value = r
            models.append(m)

        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = models

        result = repository.list_by_bot_id(100, "test_tenant", "prod")

        assert len(result) == 3
        assert all(isinstance(r, BotDeviceRelRecord) for r in result)
        assert result[0].device_uuid == "dev-001"
        assert result[1].device_uuid == "dev-002"
        assert result[2].device_uuid == "dev-003"
        mock_session.query.assert_called_once_with(BotDeviceRelModel)

    def test_empty_result(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_bot_id(999, "test_tenant", "prod")

        assert result == []


# ==================== get_by_device_uuid ====================


class TestGetByDeviceUuid:
    def test_found(self, repository, mock_session):
        mock_record = MagicMock(spec=BotDeviceRelRecord)
        mock_record.id = 7
        mock_record.bot_id = 300
        mock_record.device_uuid = "dev-xyz"

        mock_model = MagicMock()
        mock_model.to_record.return_value = mock_record

        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_model

        result = repository.get_by_device_uuid("dev-xyz", "test_tenant", "prod")

        assert result is not None
        assert isinstance(result, BotDeviceRelRecord)
        assert result.id == 7
        assert result.bot_id == 300
        assert result.device_uuid == "dev-xyz"
        mock_session.query.assert_called_once_with(BotDeviceRelModel)
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_by_device_uuid("nonexistent", "test_tenant", "prod")

        assert result is None


# ==================== soft_delete ====================


class TestSoftDelete:
    def test_executes_update_with_correct_params(self, repository, mock_session):
        repository.soft_delete(
            rel_id=10, tenant="test_tenant", env="prod", modifier="admin"
        )

        mock_session.query.assert_called_once_with(BotDeviceRelModel)
        # Verify the update was called
        chain = mock_session.query.return_value.filter.return_value
        chain.update.assert_called_once()
        call_kwargs = chain.update.call_args
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == 10  # is_deleted = rel_id per D-04
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict
        assert call_kwargs[1] == {"synchronize_session": False}


# ==================== exists ====================


class TestExists:
    def test_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1

        result = repository.exists(
            bot_id=100, device_uuid="dev-001", tenant="test_tenant", env="prod"
        )

        assert result is True
        mock_session.query.assert_called_once()

    def test_not_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repository.exists(
            bot_id=100, device_uuid="dev-002", tenant="test_tenant", env="prod"
        )

        assert result is False

    def test_scalar_returns_none(self, repository, mock_session):
        """Edge case: scalar returns None → bool(None) = False."""
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        result = repository.exists(
            bot_id=100, device_uuid="dev-003", tenant="test_tenant", env="prod"
        )

        assert result is False


# ==================== count_by_bot_id ====================


class TestCountByBotId:
    def test_counts_correctly(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 5

        result = repository.count_by_bot_id(100, "test_tenant", "prod")

        assert result == 5
        mock_session.query.assert_called_once()

    def test_zero_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repository.count_by_bot_id(999, "test_tenant", "prod")

        assert result == 0

    def test_scalar_returns_none(self, repository, mock_session):
        """Edge case: scalar returns None → int(None) raises. But count returns int, so None shouldn't happen.
        Test that the method handles the scalar correctly."""
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        # int(None) raises TypeError — the ORM method does int(count).
        # This verifies the call chain, not the type conversion.
        with pytest.raises(TypeError):
            repository.count_by_bot_id(100, "test_tenant", "prod")


# ==================== soft_delete_by_bot_id ====================


class TestSoftDeleteByBotId:
    def test_deletes_multiple_and_returns_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 3

        result = repository.soft_delete_by_bot_id(
            bot_id=100, tenant="test_tenant", env="prod", modifier="admin"
        )

        assert result == 3
        mock_session.query.assert_called_once_with(BotDeviceRelModel)
        chain = mock_session.query.return_value.filter.return_value
        chain.update.assert_called_once()
        call_kwargs = chain.update.call_args
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == BotDeviceRelModel.id
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict
        assert call_kwargs[1] == {"synchronize_session": False}

    def test_zero_affected(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.soft_delete_by_bot_id(
            bot_id=999, tenant="test_tenant", env="prod", modifier="admin"
        )

        assert result == 0


# ==================== batch_insert_rels ====================


class TestBatchInsertRels:
    def test_inserts_multiple_and_returns_ids(self, repository, mock_session):
        device_uuids = ["dev-001", "dev-002", "dev-003"]

        # Each add/flush gives the model an id
        id_counter = {"count": 10}

        def _add_side_effect(model):
            id_counter["count"] += 1
            model.id = id_counter["count"]

        mock_session.add.side_effect = _add_side_effect

        result = repository.batch_insert_rels(
            bot_id=100,
            device_uuids=device_uuids,
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
        )

        assert result == [11, 12, 13]
        assert mock_session.add.call_count == 3
        assert mock_session.flush.call_count == 3
        # Verify each call had correct model fields
        for i, call_args in enumerate(mock_session.add.call_args_list):
            model = call_args[0][0]
            assert isinstance(model, BotDeviceRelModel)
            assert model.bot_id == 100
            assert model.device_uuid == device_uuids[i]
            assert model.tenant == "test_tenant"
            assert model.env == "prod"

    def test_batch_with_incrementing_ids(self, repository, mock_session):
        id_counter = {"count": 99}

        def _add_side_effect(model):
            id_counter["count"] += 1
            model.id = id_counter["count"]

        mock_session.add.side_effect = _add_side_effect

        result = repository.batch_insert_rels(
            bot_id=200,
            device_uuids=["dev-001", "dev-002"],
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
        )

        assert result == [100, 101]
        assert mock_session.add.call_count == 2

    def test_empty_device_uuids_returns_empty_list(self, repository, mock_session):
        result = repository.batch_insert_rels(
            bot_id=100,
            device_uuids=[],
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
        )

        assert result == []
        mock_session.add.assert_not_called()


# ==================== Model.to_record ====================


class TestModelToRecord:
    def test_converts_to_valid_record(self):
        """Verify BotDeviceRelModel.to_record() produces a correct BotDeviceRelRecord."""
        from datetime import datetime

        now = datetime.now()
        model = BotDeviceRelModel(
            id=7,
            gmt_create=now,
            gmt_modified=now,
            tenant="tenant-x",
            env="staging",
            domain="bots",
            is_deleted=0,
            creator="creator-user",
            modifier="modifier-user",
            bot_id=42,
            device_uuid="dev-full-test",
        )

        record = model.to_record()

        assert isinstance(record, BotDeviceRelRecord)
        assert record.id == 7
        assert record.gmt_create == now
        assert record.gmt_modified == now
        assert record.tenant == "tenant-x"
        assert record.env == "staging"
        assert record.domain == "bots"
        assert record.is_deleted == 0
        assert record.creator == "creator-user"
        assert record.modifier == "modifier-user"
        assert record.bot_id == 42
        assert record.device_uuid == "dev-full-test"

    def test_is_deleted_none_defaults_to_zero(self):
        """Edge case: is_deleted is None → defaults to 0."""
        from datetime import datetime

        now = datetime.now()
        model = BotDeviceRelModel(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            tenant="test",
            env="prod",
            domain="default",
            is_deleted=None,
            creator="admin",
            modifier="admin",
            bot_id=1,
            device_uuid="dev-001",
        )

        record = model.to_record()

        assert record.is_deleted == 0
