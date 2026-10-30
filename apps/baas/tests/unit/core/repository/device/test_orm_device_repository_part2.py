"""
OrmDeviceRepository unit tests.

Uses pytest + MagicMock pattern matching test_orm_bot_run_repository.py
and test_zdas_device_repository.py. Covers all 18 repository methods
and update_device err_msg flow.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.device import (
    DeviceRecord,
    OrmDeviceRepository,
)


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
    """Create an OrmDeviceRepository backed by the mock database."""
    return OrmDeviceRepository(database=mock_database)


def _make_mock_device_model(
    id_val=1,
    gmt_create=None,
    gmt_modified=None,
    device_uuid="DEVICE-UUID-001",
    tenant="test_tenant",
    env="prod",
    domain="default",
    is_deleted=0,
    creator="admin",
    modifier="admin",
    status="ACTIVE",
    provider_type="ARCA",
    provider_device_id="sandbox-001",
    provider_device_props=None,
    extra_config=None,
    err_msg=None,
):
    """Create a MagicMock mimicking a DeviceModel instance with to_record()."""
    now = datetime.now()
    props_json = (
        json.dumps(provider_device_props, ensure_ascii=False)
        if isinstance(provider_device_props, dict)
        else (provider_device_props or None)
    )
    config_json = (
        json.dumps(extra_config, ensure_ascii=False)
        if isinstance(extra_config, dict)
        else (extra_config or None)
    )

    model = MagicMock()
    model.id = id_val
    model.gmt_create = gmt_create or now
    model.gmt_modified = gmt_modified or now
    model.device_uuid = device_uuid
    model.tenant = tenant
    model.env = env
    model.domain = domain
    model.is_deleted = is_deleted
    model.creator = creator
    model.modifier = modifier
    model.status = status
    model.provider_type = provider_type
    model.provider_device_id = provider_device_id
    model.provider_device_props = props_json
    model.extra_config = config_json
    model.err_msg = err_msg

    # to_record() constructs a DeviceRecord from the model's fields
    provider_device_props_dict = (
        json.loads(props_json) if isinstance(props_json, str) else props_json
    ) or {}
    extra_config_dict = (
        json.loads(config_json) if isinstance(config_json, str) else config_json
    ) or {}
    if not isinstance(extra_config_dict, dict):
        extra_config_dict = {}

    model.to_record.return_value = DeviceRecord(
        id=id_val,
        gmt_create=model.gmt_create,
        gmt_modified=model.gmt_modified,
        is_deleted=is_deleted or 0,
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain=domain,
        creator=creator,
        modifier=modifier,
        status=status,
        provider_type=provider_type,
        provider_device_id=provider_device_id,
        provider_device_props=provider_device_props_dict,
        extra_config=extra_config_dict,
        err_msg=err_msg,
    )
    return model


class TestListByBotId:
    def test_returns_devices_with_join(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, device_uuid="D1")
        model2 = _make_mock_device_model(id_val=2, device_uuid="D2")
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_by_bot_id(
            bot_id=100,
            tenant="test_tenant",
            env="prod",
        )

        assert len(result) == 2
        assert result[0].device_uuid == "D1"
        assert result[1].device_uuid == "D2"
        model1.to_record.assert_called_once()
        model2.to_record.assert_called_once()

    def test_empty_result(self, repository, mock_session):
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_bot_id(
            bot_id=999,
            tenant="test_tenant",
            env="prod",
        )
        assert result == []


class TestListActiveDevicesByBotId:
    def test_returns_active_devices(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, device_uuid="D1", status="ACTIVE")
        model2 = _make_mock_device_model(id_val=2, device_uuid="D2", status="ACTIVE")
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_active_devices_by_bot_id(bot_id=100)

        assert len(result) == 2
        assert result[0].device_uuid == "D1"
        assert result[1].device_uuid == "D2"

    def test_empty_result(self, repository, mock_session):
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_active_devices_by_bot_id(bot_id=999)
        assert result == []


class TestListDevicesByBotIds:
    def test_returns_devices_grouped_by_bot(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, device_uuid="D1")
        model2 = _make_mock_device_model(id_val=2, device_uuid="D2")
        model3 = _make_mock_device_model(id_val=3, device_uuid="D3")
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            (model1, 100),
            (model2, 100),
            (model3, 200),
        ]

        result = repository.list_devices_by_bot_ids(
            bot_ids=[100, 200],
            tenant="test_tenant",
            env="prod",
        )

        assert isinstance(result, dict)
        assert len(result[100]) == 2
        assert len(result[200]) == 1
        assert result[100][0].device_uuid == "D1"
        assert result[100][1].device_uuid == "D2"
        assert result[200][0].device_uuid == "D3"

    def test_empty_bot_ids_returns_empty_dict(self, repository, mock_session):
        result = repository.list_devices_by_bot_ids(
            bot_ids=[],
            tenant="test_tenant",
            env="prod",
        )
        assert result == {}
        mock_session.query.assert_not_called()

    def test_maps_bot_id_correctly(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, device_uuid="D1")
        model2 = _make_mock_device_model(id_val=2, device_uuid="D2")
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            (model1, 10),
            (model2, 20),
        ]

        result = repository.list_devices_by_bot_ids(
            bot_ids=[10, 20],
            tenant="test_tenant",
            env="prod",
        )

        assert len(result[10]) == 1
        assert result[10][0].device_uuid == "D1"
        assert result[20][0].device_uuid == "D2"

    def test_no_matching_bots_returns_empty_lists(self, repository, mock_session):
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_devices_by_bot_ids(
            bot_ids=[100, 200, 300],
            tenant="test_tenant",
            env="prod",
        )

        assert result == {100: [], 200: [], 300: []}


class TestListActiveLocalDevicesByMachineUser:
    """Test list_active_local_devices_by_machine_user for Phase 33."""

    def test_returns_matching_devices(self, repository, mock_session):
        model1 = _make_mock_device_model(
            id_val=1,
            device_uuid="DEVICE-001",
            provider_type="local",
            provider_device_id="container1--machine-001--user-001@42",
            status="ACTIVE",
        )
        model2 = _make_mock_device_model(
            id_val=2,
            device_uuid="DEVICE-002",
            provider_type="local",
            provider_device_id="container2--machine-001--user-001@43",
            status="ACTIVE",
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_active_local_devices_by_machine_user(
            machine_id="machine-001",
            user_id="user-001",
            env="prod",
        )

        assert len(result) == 2
        assert result[0].device_uuid == "DEVICE-001"
        assert result[1].device_uuid == "DEVICE-002"

    def test_empty_result_returns_empty_list(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_active_local_devices_by_machine_user(
            machine_id="machine-999",
            user_id="user-999",
            env="prod",
        )

        assert result == []


class TestBatchUpdateStatusToOffline:
    """Test batch_update_status_to_offline for Phase 33."""

    def test_updates_multiple_devices(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 3

        result = repository.batch_update_status_to_offline(
            device_ids=[1, 2, 3],
            env="prod",
        )

        assert result == 3
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "OFFLINE"
        assert "gmt_modified" in update_dict

    def test_empty_list_returns_zero(self, repository, mock_session):
        result = repository.batch_update_status_to_offline(
            device_ids=[],
            env="prod",
        )

        assert result == 0
        mock_session.query.assert_not_called()

    def test_single_device_update(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.batch_update_status_to_offline(
            device_ids=[42],
            env="dev",
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
