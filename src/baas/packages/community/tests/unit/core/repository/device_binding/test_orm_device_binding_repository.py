"""
OrmDeviceBindingRepository unit tests for list_bindings_by_ttl_asc
and list_baas_devices_by_ttl_asc (DeviceTtlTimer queries).
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.core.repository.device_binding import (
    DeviceBindingRecord,
    OrmDeviceBindingRepository,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Create a mock SQLAlchemy session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_database(mock_session):
    """Create a mock database that returns the mock session via orm_session context manager."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture
def repository(mock_database):
    return OrmDeviceBindingRepository(mock_database)


def _make_binding_model(
    id_val=1,
    entity_id="entity-001",
    entity_type="bot",
    device_id="device-001",
    device_provider="arca",
    env="prod",
    device_props=None,
    status="ACTIVE",
    apply_reason="test",
    applied_by="tester",
    release_reason=None,
    released_by=None,
    released_at=None,
    last_alive_at=None,
    gmt_create=None,
    gmt_modified=None,
):
    """Create a mock DeviceBindingModel."""
    now = datetime.now()
    model = MagicMock()
    model.id = id_val
    model.entity_id = entity_id
    model.entity_type = entity_type
    model.device_id = device_id
    model.device_provider = device_provider
    model.env = env
    model.device_props = (
        json.dumps(device_props, ensure_ascii=False)
        if isinstance(device_props, dict)
        else (device_props or "{}")
    )
    model.status = status
    model.apply_reason = apply_reason
    model.applied_by = applied_by
    model.release_reason = release_reason
    model.released_by = released_by
    model.released_at = released_at
    model.last_alive_at = last_alive_at
    model.gmt_create = gmt_create or now
    model.gmt_modified = gmt_modified or now
    return model


def _make_device_model(
    id_val=1,
    tenant="tenant-1",
    env="prod",
    status="ACTIVE",
    provider_type="ARCA",
    provider_device_id="sbx-001",
    provider_device_props=None,
    is_deleted=0,
):
    """Create a mock DeviceModel (baas_device)."""
    model = MagicMock()
    model.id = id_val
    model.tenant = tenant
    model.env = env
    model.status = status
    model.provider_type = provider_type
    model.provider_device_id = provider_device_id
    model.provider_device_props = (
        json.dumps(provider_device_props, ensure_ascii=False)
        if isinstance(provider_device_props, dict)
        else (provider_device_props or "{}")
    )
    model.is_deleted = is_deleted
    # to_record returns a MagicMock with attribute access for dict conversion
    record = MagicMock()
    record.id = model.id
    record.tenant = model.tenant
    record.env = model.env
    record.status = model.status
    record.provider_type = model.provider_type
    record.provider_device_id = model.provider_device_id
    record.provider_device_props = model.provider_device_props
    record.is_deleted = model.is_deleted
    model.to_record.return_value = record
    return model


# ==================== Tests ====================


class TestOrmListBindingsByTtlAsc:
    """Tests for OrmDeviceBindingRepository.list_bindings_by_ttl_asc."""

    def test_returns_records_ordered_by_ttl(
        self, repository, mock_database, mock_session
    ):
        model1 = _make_binding_model(
            id_val=1,
            device_id="device-001",
            device_props='{"sandbox_id": "sb-1", "ttl_expiration_time": "2026-01-01 00:00:00"}',
        )
        model2 = _make_binding_model(
            id_val=2,
            device_id="device-002",
            device_props='{"sandbox_id": "sb-2", "ttl_expiration_time": "2026-06-01 00:00:00"}',
        )

        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_bindings_by_ttl_asc(limit=100)

        assert len(result) == 2
        assert isinstance(result[0], DeviceBindingRecord)
        assert result[0].id == 1
        assert result[1].id == 2

    def test_returns_empty_list(self, repository, mock_database, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repository.list_bindings_by_ttl_asc(limit=50)

        assert result == []

    def test_skips_none_records(self, repository, mock_database, mock_session):
        model1 = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            None,
        ]

        result = repository.list_bindings_by_ttl_asc(limit=100)

        assert len(result) == 1
        assert result[0].id == 1


class TestOrmListBaasDevicesByTtlAsc:
    """Tests for OrmDeviceBindingRepository.list_baas_devices_by_ttl_asc."""

    def test_returns_dicts_ordered_by_ttl(
        self, repository, mock_database, mock_session
    ):
        device1 = _make_device_model(
            id_val=1,
            provider_device_id="sbx-001",
            provider_device_props='{"sandbox_id": "sbx-001", "ttl_expiration_time": "2026-01-01 00:00:00"}',
        )
        device2 = _make_device_model(
            id_val=2,
            provider_device_id="sbx-002",
            provider_device_props='{"sandbox_id": "sbx-002", "ttl_expiration_time": "2026-12-01 00:00:00"}',
        )

        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            device1,
            device2,
        ]

        result = repository.list_baas_devices_by_ttl_asc(limit=100)

        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert result[0]["id"] == 1
        assert result[0]["provider_device_id"] == "sbx-001"
        assert result[1]["id"] == 2

    def test_returns_empty_list(self, repository, mock_database, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repository.list_baas_devices_by_ttl_asc(limit=50)

        assert result == []

    def test_result_has_expected_keys(self, repository, mock_database, mock_session):
        device = _make_device_model(
            id_val=10,
            tenant="test-tenant",
            env="pre",
            status="ACTIVE",
            provider_type="ARCA",
            provider_device_id="sbx-abc",
            provider_device_props='{"sandbox_id": "sbx-abc"}',
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            device
        ]

        result = repository.list_baas_devices_by_ttl_asc(limit=10)

        assert len(result) == 1
        expected_keys = {
            "id",
            "tenant",
            "env",
            "status",
            "provider_type",
            "provider_device_id",
            "provider_device_props",
            "is_deleted",
        }
        assert set(result[0].keys()) == expected_keys
