"""
Comprehensive unit tests for OrmDeviceBindingRepository.

Covers all methods in _orm_repository.py to improve code coverage from ~24%.
Uses the same mock_session / mock_database / repository fixture pattern
as the existing test file.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.device_binding import (
    DeviceBindingRecord,
    OrmDeviceBindingRepository,
)


@pytest.fixture
def mock_session():
    """Create a mock SQLAlchemy session."""
    session = MagicMock()
    # with_orm_session decorator accesses session.is_active and session.connection()
    session.is_active = True
    session.connection.return_value.connection = MagicMock()
    # Make query().filter() return query() itself so chained filter calls
    # stay on the same mock object (SQLAlchemy Query.filter returns a new Query,
    # but for mocking purposes we collapse the chain onto one object).
    session.query.return_value.filter.return_value = session.query.return_value
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


def _make_execute_result(rows=None, scalar_val=None):
    """Create a mock result object for session.execute(text(...))."""
    result = MagicMock()
    if rows is not None:
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
    else:
        result.fetchall.return_value = []
        result.fetchone.return_value = None
    result.scalar.return_value = scalar_val
    return result


class TestUpdateBaasDeviceTtlById:
    def test_update(self, repository, mock_session):
        repository.update_baas_device_ttl_by_id(
            baas_device_id=1,
            ttl_expiration_time="2026-01-01 00:00:00",
            ttl_expiration_timestamp=1234567890,
            refresh_fail_count=2,
        )

        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["baas_device_id"] == 1
        assert params["ttl_expiration_time"] == "2026-01-01 00:00:00"
        assert params["ttl_expiration_timestamp"] == 1234567890
        assert params["refresh_fail_count"] == 2

    def test_default_refresh_fail_count(self, repository, mock_session):
        repository.update_baas_device_ttl_by_id(
            baas_device_id=1,
            ttl_expiration_time="2026-01-01 00:00:00",
            ttl_expiration_timestamp=1234567890,
        )

        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["refresh_fail_count"] == 0


class TestUpdateBaasDeviceRefreshFailCountById:
    def test_update(self, repository, mock_session):
        repository.update_baas_device_refresh_fail_count_by_id(
            baas_device_id=1, refresh_fail_count=5
        )

        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["baas_device_id"] == 1
        assert params["refresh_fail_count"] == 5


class TestGetBaasDeviceById:
    def test_found(self, repository, mock_session):
        device = _make_device_model(id_val=1)
        mock_session.query.return_value.filter.return_value.first.return_value = device

        result = repository.get_baas_device_by_id(baas_device_id=1)

        assert result is not None
        assert result["id"] == 1
        assert result["tenant"] == "tenant-1"
        assert result["env"] == "prod"
        assert result["status"] == "ACTIVE"
        assert result["provider_type"] == "ARCA"
        assert result["provider_device_id"] == "sbx-001"
        assert result["is_deleted"] == 0

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_baas_device_by_id(baas_device_id=999)

        assert result is None


class TestListBaasDevicesActivePaginated:
    def test_returns_items(self, repository, mock_session):
        device1 = _make_device_model(id_val=1)
        device2 = _make_device_model(id_val=2)

        # The query chain: query -> filter -> count() and query -> filter -> order_by -> offset -> limit -> all()
        q = mock_session.query.return_value
        q.filter.return_value.count.return_value = 2
        q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            device1,
            device2,
        ]

        total, items = repository.list_baas_devices_active_paginated(
            env="prod", page=1, page_size=20
        )

        assert total == 2
        assert len(items) == 2
        assert items[0]["id"] == 1
        assert items[1]["id"] == 2

    def test_empty(self, repository, mock_session):
        q = mock_session.query.return_value
        q.filter.return_value.count.return_value = 0
        q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_baas_devices_active_paginated(
            env="prod", page=1, page_size=20
        )

        assert total == 0
        assert items == []

    def test_pagination(self, repository, mock_session):
        q = mock_session.query.return_value
        q.filter.return_value.count.return_value = 50
        q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_baas_devices_active_paginated(
            env="prod", page=3, page_size=10
        )

        assert total == 50
        # offset should be (3-1)*10 = 20
        q.filter.return_value.order_by.return_value.offset.assert_called_with(20)
        q.filter.return_value.order_by.return_value.offset.return_value.limit.assert_called_with(
            10
        )


class TestListBaasDevicesByIdAsc:
    def test_returns_dicts(self, repository, mock_session):
        device1 = _make_device_model(id_val=1, provider_device_id="sbx-001")
        device2 = _make_device_model(id_val=2, provider_device_id="sbx-002")

        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            device1,
            device2,
        ]

        result = repository.list_baas_devices_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=100
        )

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["provider_device_id"] == "sbx-001"
        assert result[1]["id"] == 2

    def test_empty(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repository.list_baas_devices_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=50
        )

        assert result == []

    def test_result_has_expected_keys(self, repository, mock_session):
        device = _make_device_model(id_val=10, tenant="test-tenant", env="pre")
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            device
        ]

        result = repository.list_baas_devices_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=10
        )

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


class TestUpdateBaasDeviceStatusById:
    def test_update(self, repository, mock_session):
        repository.update_baas_device_status_by_id(baas_device_id=1, status="INACTIVE")

        mock_session.query.return_value.filter.return_value.update.assert_called_once_with(
            {"status": "INACTIVE", "modifier": "system"}, synchronize_session=False
        )

    def test_update_with_custom_modifier(self, repository, mock_session):
        repository.update_baas_device_status_by_id(
            baas_device_id=1, status="ACTIVE", modifier="admin"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once_with(
            {"status": "ACTIVE", "modifier": "admin"}, synchronize_session=False
        )
