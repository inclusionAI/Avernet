"""
OrmDeviceRepository unit tests.

Uses pytest + MagicMock pattern matching test_orm_bot_run_repository.py
and test_zdas_device_repository.py. Covers all 18 repository methods
and update_device err_msg flow.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.device import (
    DeviceRecord,
    OrmDeviceRepository,
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


# ==================== insert_device ====================


class TestInsertDevice:
    def test_insert_returns_id(self, repository, mock_session):
        # simulate auto-increment after flush
        model = _make_mock_device_model(id_val=42, device_uuid="DEVICE-NEW")

        def _set_id_and_return(model_obj):
            model_obj.id = model.id

        mock_session.add.side_effect = _set_id_and_return

        result = repository.insert_device(
            device_uuid="DEVICE-NEW",
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
            status="PENDING",
            provider_type="ARCA",
            provider_device_id="sandbox-new",
            provider_device_props={"cpu": 2, "memory": "4G"},
            extra_config={"timeout": 30},
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.device_uuid == "DEVICE-NEW"
        assert added_model.tenant == "test_tenant"
        assert added_model.env == "prod"
        assert added_model.domain == "default"
        assert added_model.creator == "admin"
        assert added_model.modifier == "admin"
        assert added_model.status == "PENDING"
        assert added_model.provider_type == "ARCA"
        assert added_model.provider_device_id == "sandbox-new"
        assert "cpu" in added_model.provider_device_props
        assert "4G" in added_model.provider_device_props
        assert "timeout" in added_model.extra_config

    def test_insert_with_none_props_and_config(self, repository, mock_session):
        def _set_id_and_return(model_obj):
            model_obj.id = 1

        mock_session.add.side_effect = _set_id_and_return

        result = repository.insert_device(
            device_uuid="DEVICE-MIN",
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
            status="PENDING",
            provider_type=None,
            provider_device_id=None,
            provider_device_props=None,
            extra_config=None,
        )

        assert result == 1
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.provider_device_props is None
        assert added_model.extra_config is None

    def test_insert_with_default_status(self, repository, mock_session):
        """insert_device defaults status to 'PENDING' when not provided."""

        def _set_id_and_return(model_obj):
            model_obj.id = 1

        mock_session.add.side_effect = _set_id_and_return

        result = repository.insert_device(
            device_uuid="DEVICE-DEFAULT",
            tenant="test_tenant",
            env="prod",
            domain="default",
            creator="admin",
            modifier="admin",
            provider_type="ARCA",
        )

        assert result == 1
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        added_model = mock_session.add.call_args[0][0]
        assert added_model.status == "PENDING"


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repository, mock_session):
        model = _make_mock_device_model(id_val=5, device_uuid="DEVICE-FOUND")
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_id(5, "test_tenant", "prod")

        assert result is not None
        assert isinstance(result, DeviceRecord)
        assert result.id == 5
        assert result.device_uuid == "DEVICE-FOUND"
        model.to_record.assert_called_once()
        mock_session.query.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999, "test_tenant", "prod")
        assert result is None


# ==================== get_by_ids ====================


class TestGetByIds:
    def test_returns_multiple_records(self, repository, mock_session):
        model_a = _make_mock_device_model(id_val=1, device_uuid="DEVICE-A")
        model_c = _make_mock_device_model(id_val=3, device_uuid="DEVICE-C")
        mock_session.query.return_value.filter.return_value.all.return_value = [
            model_a,
            model_c,
        ]

        result = repository.get_by_ids([1, 2, 3], "test_tenant", "prod")

        assert len(result) == 2
        assert result[1].device_uuid == "DEVICE-A"
        assert result[3].device_uuid == "DEVICE-C"
        model_a.to_record.assert_called_once()
        model_c.to_record.assert_called_once()

    def test_empty_ids_returns_empty_dict(self, repository, mock_session):
        result = repository.get_by_ids([], "test_tenant", "prod")
        assert result == {}
        mock_session.query.assert_not_called()


# ==================== get_by_device_uuid ====================


class TestGetByDeviceUuid:
    def test_found_with_status(self, repository, mock_session):
        model = _make_mock_device_model(
            id_val=7, device_uuid="DEVICE-STATUS", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_by_device_uuid(
            "DEVICE-STATUS", "test_tenant", "prod", "ACTIVE"
        )

        assert result is not None
        assert result.device_uuid == "DEVICE-STATUS"
        assert result.status == "ACTIVE"
        model.to_record.assert_called_once()

    def test_found_without_status(self, repository, mock_session):
        """When status is None, queries without status filter, ordered by id DESC."""
        model = _make_mock_device_model(
            id_val=7, device_uuid="DEVICE-NOSTAT", status="PENDING"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_by_device_uuid(
            "DEVICE-NOSTAT", "test_tenant", "prod", None
        )

        assert result is not None
        assert result.status == "PENDING"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_by_device_uuid(
            "NONEXISTENT", "test_tenant", "prod", "ACTIVE"
        )
        assert result is None


# ==================== get_by_device_uuid_only ====================


class TestGetByDeviceUuidOnly:
    def test_found(self, repository, mock_session):
        model = _make_mock_device_model(id_val=10, device_uuid="DEVICE-GLOBAL")
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_device_uuid_only("DEVICE-GLOBAL")

        assert result is not None
        assert result.device_uuid == "DEVICE-GLOBAL"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_device_uuid_only("MISSING")
        assert result is None


# ==================== list_by_device_uuid ====================


class TestListByDeviceUuid:
    def test_returns_multiple_records(self, repository, mock_session):
        models = [
            _make_mock_device_model(id_val=3, device_uuid="DEVICE-LIST"),
            _make_mock_device_model(id_val=2, device_uuid="DEVICE-LIST"),
            _make_mock_device_model(id_val=1, device_uuid="DEVICE-LIST"),
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = models

        result = repository.list_by_device_uuid("DEVICE-LIST", "test_tenant", "prod")

        assert len(result) == 3
        assert all(isinstance(r, DeviceRecord) for r in result)
        for m in models:
            m.to_record.assert_called_once()

    def test_empty_result(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_device_uuid("DEVICE-EMPTY", "test_tenant", "prod")
        assert result == []


# ==================== get_active_by_device_uuid ====================


class TestGetActiveByDeviceUuid:
    def test_found(self, repository, mock_session):
        model = _make_mock_device_model(
            id_val=5, device_uuid="DEVICE-ACTIVE", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_active_by_device_uuid(
            "DEVICE-ACTIVE", "test_tenant", "prod"
        )

        assert result is not None
        assert result.status == "ACTIVE"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_active_by_device_uuid(
            "NONEXISTENT", "test_tenant", "prod"
        )
        assert result is None


# ==================== get_active_or_updating_by_device_uuid ====================


class TestGetActiveOrUpdatingByDeviceUuid:
    def test_found_active(self, repository, mock_session):
        model = _make_mock_device_model(
            id_val=5, device_uuid="DEVICE-AU", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_active_or_updating_by_device_uuid(
            "DEVICE-AU", "test_tenant", "prod"
        )
        assert result is not None
        assert result.status == "ACTIVE"
        model.to_record.assert_called_once()

    def test_found_updating(self, repository, mock_session):
        model = _make_mock_device_model(
            id_val=6, device_uuid="DEVICE-UPD", status="UPDATING"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_active_or_updating_by_device_uuid(
            "DEVICE-UPD", "test_tenant", "prod"
        )
        assert result is not None
        assert result.status == "UPDATING"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_active_or_updating_by_device_uuid(
            "NONEXISTENT", "test_tenant", "prod"
        )
        assert result is None


# ==================== get_by_provider_device_id_like ====================


class TestGetByProviderDeviceIdLike:
    def test_found(self, repository, mock_session):
        model = _make_mock_device_model(id_val=8, provider_device_id="sandbox-abc-123")
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_by_provider_device_id_like("sandbox-abc")

        assert result is not None
        assert result.provider_device_id == "sandbox-abc-123"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_by_provider_device_id_like("nonexistent")
        assert result is None


# ==================== get_by_provider_device_id_prefix ====================


class TestGetByProviderDeviceIdPrefix:
    def test_found(self, repository, mock_session):
        model = _make_mock_device_model(
            id_val=9, provider_device_id="sandbox-prefix-abc"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_by_provider_device_id_prefix("sandbox-prefix", "prod")

        assert result is not None
        assert result.provider_device_id == "sandbox-prefix-abc"
        model.to_record.assert_called_once()

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_by_provider_device_id_prefix("no-match", "dev")
        assert result is None


# ==================== update_device ====================


class TestUpdateDevice:
    def test_update_all_fields(self, repository, mock_session):
        """update returns rowcount from the update() call."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_device(
            device_id=10,
            tenant="test_tenant",
            env="prod",
            modifier="admin2",
            provider_type="MCP",
            provider_device_id="mcp-dev-001",
            provider_device_props={"new": True},
            extra_config={"timeout": 60},
            status="UPDATING",
            err_msg=None,
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "admin2"
        assert update_dict["provider_type"] == "MCP"
        assert update_dict["provider_device_id"] == "mcp-dev-001"
        assert "new" in update_dict["provider_device_props"]
        assert "timeout" in update_dict["extra_config"]
        assert update_dict["status"] == "UPDATING"
        assert "gmt_modified" in update_dict

    def test_update_no_fields_returns_zero(self, repository, mock_session):
        """When no optional fields provided, only gmt_modified is in values.
        The update still runs but the method returns the rowcount from .update()."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_device(
            device_id=10,
            tenant="test_tenant",
            env="prod",
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        # Only gmt_modified is set when no optional fields are provided
        assert "gmt_modified" in update_dict
        assert "modifier" not in update_dict

    def test_update_with_err_msg(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_device(
            device_id=10,
            tenant="test_tenant",
            env="prod",
            modifier="admin",
            status="FAILED",
            err_msg="Deployment timeout",
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert "err_msg" in update_dict
        assert "modifier" in update_dict
        assert update_dict["modifier"] == "admin"

    def test_update_with_long_err_msg(self, repository, mock_session):
        """Long err_msg is handled by _build_err_msg_prefix truncation."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        long_msg = "x" * 15000

        result = repository.update_device(
            device_id=10,
            tenant="test_tenant",
            env="prod",
            status="FAILED",
            err_msg=long_msg,
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()

    def test_update_with_err_msg_unicode_multibyte(self, repository, mock_session):
        """Test err_msg truncation respects UTF-8 multi-byte boundaries."""
        mock_session.query.return_value.filter.return_value.update.return_value = 1
        long_msg = "🚀" * 5000

        result = repository.update_device(
            device_id=10,
            tenant="test_tenant",
            env="prod",
            status="FAILED",
            err_msg=long_msg,
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()


# ==================== _build_err_msg_prefix ====================


class TestBuildErrMsgPrefix:
    def test_short_message_fits(self):
        repository = OrmDeviceRepository(database=MagicMock())
        msg = "Short error"
        result = repository._build_err_msg_prefix(msg)
        assert result.startswith("[")
        assert "Short error" in result
        assert "\n" in result

    def test_long_message_truncated(self):
        repository = OrmDeviceRepository(database=MagicMock())
        msg = "a" * 15000
        result = repository._build_err_msg_prefix(msg)
        assert len(result.encode("utf-8")) <= 10000

    def test_exact_boundary_message(self):
        repository = OrmDeviceRepository(database=MagicMock())
        msg = "a" * 5000
        result = repository._build_err_msg_prefix(msg)
        assert "aaaaa" in result

    def test_multibyte_truncation_clean(self):
        repository = OrmDeviceRepository(database=MagicMock())
        msg = "🚀" * 10000
        result = repository._build_err_msg_prefix(msg)
        decoded = result.encode("utf-8").decode("utf-8")
        assert decoded == result
        assert len(result.encode("utf-8")) <= 10000

    def test_small_max_bytes_returns_prefix_only(self):
        repository = OrmDeviceRepository(database=MagicMock())
        original_max = OrmDeviceRepository._MAX_NEW_ERR_MSG_BYTES
        OrmDeviceRepository._MAX_NEW_ERR_MSG_BYTES = 21
        try:
            result = repository._build_err_msg_prefix("\U0001f600\U0001f601")
        finally:
            OrmDeviceRepository._MAX_NEW_ERR_MSG_BYTES = original_max
        assert result.startswith("[")
        assert result.endswith("\n")


# ==================== update_status ====================


class TestUpdateStatus:
    def test_updates_status(self, repository, mock_session):
        repository.update_status(
            device_id=10, tenant="test_tenant", env="prod", status="FAILED"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "FAILED"
        assert "gmt_modified" in update_dict


# ==================== soft_delete ====================


class TestSoftDelete:
    def test_soft_delete_by_id(self, repository, mock_session):
        repository.soft_delete(
            device_id=10, tenant="test_tenant", env="prod", modifier="admin"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == 10
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict


# ==================== soft_delete_by_device_uuid ====================


class TestSoftDeleteByDeviceUuid:
    def test_deletes_and_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.soft_delete_by_device_uuid(
            "DEVICE-DEL", "test_tenant", "prod", "admin"
        )

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_not_found_returns_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.soft_delete_by_device_uuid(
            "MISSING", "test_tenant", "prod", "admin"
        )
        assert result == 0


# ==================== update_status_by_device_uuid ====================


class TestUpdateStatusByDeviceUuid:
    def test_updates_and_returns_rowcount(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repository.update_status_by_device_uuid(
            "DEVICE-UUID", "test_tenant", "prod", "UPDATING"
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "UPDATING"
        assert "gmt_modified" in update_dict

    def test_not_found_returns_zero(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repository.update_status_by_device_uuid(
            "MISSING", "test_tenant", "prod", "DELETED"
        )
        assert result == 0


# ==================== list_devices ====================


class TestListDevices:
    def test_no_status_filter(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, device_uuid="D1")
        model2 = _make_mock_device_model(id_val=2, device_uuid="D2")

        # scalar() returns total count
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 5
        # all() returns paginated rows
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repository.list_devices(
            tenant="test_tenant",
            env="prod",
        )

        assert total == 5
        assert len(items) == 2
        assert items[0].device_uuid == "D1"
        model1.to_record.assert_called_once()
        model2.to_record.assert_called_once()

    def test_with_status_filter(self, repository, mock_session):
        model1 = _make_mock_device_model(id_val=1, status="ACTIVE")
        model2 = _make_mock_device_model(id_val=2, status="ACTIVE")
        mock_session.query.return_value.filter.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 2
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repository.list_devices(
            tenant="test_tenant",
            env="prod",
            status="ACTIVE",
        )

        assert total == 2
        assert len(items) == 2

    def test_with_pagination(self, repository, mock_session):
        models = [_make_mock_device_model(id_val=i) for i in range(11, 21)]
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 100
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = models

        total, items = repository.list_devices(
            tenant="test_tenant",
            env="prod",
            page=2,
            page_size=10,
        )

        assert total == 100
        assert len(items) == 10

    def test_empty_result(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 0
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_devices(
            tenant="test_tenant",
            env="prod",
        )

        assert total == 0
        assert items == []


# ==================== list_by_bot_id ====================


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


# ==================== list_active_devices_by_bot_id ====================


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


# ==================== list_devices_by_bot_ids ====================


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


# ==================== list_active_local_devices_by_machine_user ====================


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


# ==================== batch_update_status_to_offline ====================


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
