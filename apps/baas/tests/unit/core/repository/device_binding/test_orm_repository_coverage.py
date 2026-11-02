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


class TestModelToRecord:
    """Tests for OrmDeviceBindingRepository._model_to_record."""

    def test_none_input_returns_none(self):
        result = OrmDeviceBindingRepository._model_to_record(None)
        assert result is None

    def test_dict_props(self):
        model = _make_binding_model(device_props={"key": "value"})
        result = OrmDeviceBindingRepository._model_to_record(model)
        assert result is not None
        assert result.device_props == {"key": "value"}

    def test_json_string_props(self):
        model = _make_binding_model(device_props='{"key": "value"}')
        result = OrmDeviceBindingRepository._model_to_record(model)
        assert result is not None
        assert result.device_props == {"key": "value"}

    def test_invalid_json_props(self):
        model = _make_binding_model(device_props="not-valid-json")
        result = OrmDeviceBindingRepository._model_to_record(model)
        assert result is not None
        assert result.device_props == {}

    def test_empty_dict_props(self):
        model = _make_binding_model(device_props={})
        result = OrmDeviceBindingRepository._model_to_record(model)
        assert result is not None
        assert result.device_props == {}

    def test_all_fields_mapped(self):
        now = datetime.now()
        model = _make_binding_model(
            id_val=42,
            entity_id="ent-1",
            entity_type="agent",
            device_id="dev-1",
            device_provider="baas",
            env="pre",
            device_props={"sandbox_id": "sbx"},
            status="PENDING",
            apply_reason="reason",
            applied_by="user1",
            release_reason="released",
            released_by="admin",
            released_at=now,
            last_alive_at=now,
            gmt_create=now,
            gmt_modified=now,
        )
        result = OrmDeviceBindingRepository._model_to_record(model)
        assert result.id == 42
        assert result.entity_id == "ent-1"
        assert result.entity_type == "agent"
        assert result.device_id == "dev-1"
        assert result.device_provider == "baas"
        assert result.env == "pre"
        assert result.device_props == {"sandbox_id": "sbx"}
        assert result.status == "PENDING"
        assert result.apply_reason == "reason"
        assert result.applied_by == "user1"
        assert result.release_reason == "released"
        assert result.released_by == "admin"
        assert result.released_at == now
        assert result.last_alive_at == now
        assert result.gmt_create == now
        assert result.gmt_modified == now


class TestInsertBinding:
    def test_insert_binding_returns_id(self, repository, mock_session):
        # DeviceBindingModel is created inside the method, so we patch __init__ to
        # capture the instance. Simpler: mock session.add sets row.id via flush.
        # We patch DeviceBindingModel so that when constructed, it gets a known id.
        with patch(
            "secbaas.community.core.repository.device_binding._orm_repository.DeviceBindingModel"
        ) as MockModel:
            mock_instance = MagicMock()
            mock_instance.id = 99
            MockModel.return_value = mock_instance

            result = repository.insert_binding(
                entity_id="ent-1",
                entity_type="bot",
                device_id="dev-1",
                device_provider="arca",
                env="prod",
                device_props={"key": "val"},
                status="PENDING",
                apply_reason="test",
                applied_by="user1",
            )

            assert result == 99
            mock_session.add.assert_called_once_with(mock_instance)
            mock_session.flush.assert_called_once()

    def test_insert_binding_serializes_props(self, repository, mock_session):
        with patch(
            "secbaas.community.core.repository.device_binding._orm_repository.DeviceBindingModel"
        ) as MockModel:
            mock_instance = MagicMock()
            mock_instance.id = 1
            MockModel.return_value = mock_instance

            repository.insert_binding(
                entity_id="ent-1",
                entity_type="bot",
                device_id="dev-1",
                device_provider="arca",
                env="prod",
                device_props={"a": "b"},
                status="ACTIVE",
                apply_reason=None,
                applied_by="user1",
            )

            # Verify DeviceBindingModel was called with serialized props
            call_kwargs = MockModel.call_args.kwargs
            assert call_kwargs["device_props"] == json.dumps(
                {"a": "b"}, ensure_ascii=False
            )
            assert call_kwargs["entity_id"] == "ent-1"
            assert call_kwargs["status"] == "ACTIVE"
            assert call_kwargs["apply_reason"] is None


class TestGetById:
    def test_found(self, repository, mock_session):
        model = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_id(1)

        assert result is not None
        assert result.id == 1
        assert isinstance(result, DeviceBindingRecord)

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_id(999)

        assert result is None


class TestGetByDeviceId:
    def test_found(self, repository, mock_session):
        model = _make_binding_model(id_val=5, device_id="dev-5")
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_by_device_id("dev-5")

        assert result is not None
        assert result.id == 5
        assert result.device_id == "dev-5"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_by_device_id("nonexistent")

        assert result is None


class TestReleaseBinding:
    def test_release_binding(self, repository, mock_session):
        repository.release_binding(
            binding_id=1, release_reason="done", released_by="admin"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        update_dict = call_args[0][0]
        assert update_dict["release_reason"] == "done"
        assert update_dict["released_by"] == "admin"
        assert call_args[1]["synchronize_session"] is False


class TestUpdateStatus:
    def test_update_status(self, repository, mock_session):
        repository.update_status(binding_id=1, status="ACTIVE")

        mock_session.query.return_value.filter.return_value.update.assert_called_once_with(
            {"status": "ACTIVE"}, synchronize_session=False
        )


class TestUpdateStatusAndAliveAt:
    def test_update_status_and_alive_at(self, repository, mock_session):
        repository.update_status_and_alive_at(binding_id=1, status="ACTIVE")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        update_dict = call_args[0][0]
        assert update_dict["status"] == "ACTIVE"
        assert "last_alive_at" in update_dict
        assert call_args[1]["synchronize_session"] is False


class TestListBindings:
    def test_no_filters(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        model2 = _make_binding_model(id_val=2)

        # The query chain: query -> [filters] -> with_entities -> scalar / order_by -> offset -> limit -> all
        q = mock_session.query.return_value
        q.with_entities.return_value.scalar.return_value = 2
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repository.list_bindings()

        assert total == 2
        assert len(items) == 2
        assert items[0].id == 1
        assert items[1].id == 2

    def test_with_filters(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1, env="prod")
        q = mock_session.query.return_value
        # Make filter() return the same mock so chained calls stay on one object
        q.filter.return_value = q
        q.with_entities.return_value.scalar.return_value = 1
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1
        ]

        total, items = repository.list_bindings(
            entity_id="ent-1",
            entity_type="bot",
            device_provider="arca",
            env="prod",
            status="ACTIVE",
            page=1,
            page_size=10,
        )

        assert total == 1
        assert len(items) == 1
        assert items[0].id == 1

    def test_empty_results(self, repository, mock_session):
        q = mock_session.query.return_value
        q.with_entities.return_value.scalar.return_value = 0
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_bindings()

        assert total == 0
        assert items == []

    def test_pagination_page2(self, repository, mock_session):
        q = mock_session.query.return_value
        q.with_entities.return_value.scalar.return_value = 50
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_bindings(page=2, page_size=20)

        assert total == 50
        assert items == []
        # Verify offset is (2-1)*20 = 20
        q.order_by.return_value.offset.assert_called_with(20)
        q.order_by.return_value.offset.return_value.limit.assert_called_with(20)


class TestListBindingsByProviders:
    def test_empty_providers(self, repository, mock_session):
        total, items = repository.list_bindings_by_providers(providers=[])

        assert total == 0
        assert items == []
        mock_session.query.assert_not_called()

    def test_with_providers(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        q = mock_session.query.return_value
        q.with_entities.return_value.scalar.return_value = 1
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1
        ]

        total, items = repository.list_bindings_by_providers(
            providers=["arca", "baas"], env="prod", status="ACTIVE"
        )

        assert total == 1
        assert len(items) == 1

    def test_with_providers_no_results(self, repository, mock_session):
        q = mock_session.query.return_value
        q.with_entities.return_value.scalar.return_value = 0
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_bindings_by_providers(providers=["arca"])

        assert total == 0
        assert items == []


class TestCountNonReleasedBindings:
    def test_returns_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 3

        result = repository.count_non_released_bindings(
            entity_id="ent-1", entity_type="bot", env="prod"
        )

        assert result == 3

    def test_returns_zero_when_none(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        result = repository.count_non_released_bindings(
            entity_id="ent-1", entity_type="bot", env="prod"
        )

        assert result == 0


class TestExistsDeviceId:
    def test_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1

        result = repository.exists_device_id(device_id="dev-1")

        assert result is True

    def test_not_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repository.exists_device_id(device_id="dev-1")

        assert result is False

    def test_none_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        result = repository.exists_device_id(device_id="dev-1")

        assert result is False


class TestGetReleasedBinding:
    def test_found(self, repository, mock_session):
        model = _make_binding_model(id_val=1, status="RELEASED")
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_released_binding(device_id="dev-1")

        assert result is not None
        assert result.id == 1

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_released_binding(device_id="dev-1")

        assert result is None


class TestReuseBinding:
    def test_reuse_binding(self, repository, mock_session):
        repository.reuse_binding(
            binding_id=1,
            device_props={"key": "val"},
            apply_reason="reuse",
            applied_by="user1",
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        update_dict = call_args[0][0]
        assert update_dict["status"] == "PENDING"
        assert update_dict["device_props"] == json.dumps(
            {"key": "val"}, ensure_ascii=False
        )
        assert update_dict["apply_reason"] == "reuse"
        assert update_dict["applied_by"] == "user1"
        assert update_dict["release_reason"] is None
        assert update_dict["released_by"] is None
        assert update_dict["released_at"] is None
        assert update_dict["last_alive_at"] is None

    def test_reuse_binding_custom_status(self, repository, mock_session):
        repository.reuse_binding(
            binding_id=2,
            device_props={},
            apply_reason=None,
            applied_by="admin",
            status="ACTIVE",
        )

        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        update_dict = call_args[0][0]
        assert update_dict["status"] == "ACTIVE"


class TestDeleteBinding:
    def test_deleted(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 1

        result = repository.delete_binding(1)

        assert result is True

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.delete.return_value = 0

        result = repository.delete_binding(999)

        assert result is False


class TestExists:
    def test_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1

        result = repository.exists(1)

        assert result is True

    def test_not_exists(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repository.exists(999)

        assert result is False

    def test_none_count(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        result = repository.exists(999)

        assert result is False


class TestListBindingsByIdAsc:
    def test_returns_records(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        model2 = _make_binding_model(id_val=2)

        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_bindings_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=100
        )

        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 2

    def test_empty_list(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repository.list_bindings_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=50
        )

        assert result == []

    def test_skips_none(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            None,
        ]

        result = repository.list_bindings_by_id_asc(
            due_before="2099-01-01 00:00:00", limit=100
        )

        assert len(result) == 1
        assert result[0].id == 1


class TestUpdateDevicePropsTtl:
    def test_update(self, repository, mock_session):
        repository.update_device_props_ttl(
            binding_id=1,
            ttl_expiration_timestamp=1234567890,
            ttl_expiration_time="2026-01-01 00:00:00",
            refresh_fail_count=0,
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        assert call_args[1]["synchronize_session"] is False
        assert "device_props" in call_args[0][0]


class TestGetBindingBySandboxId:
    def test_found(self, repository, mock_session):
        model = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_binding_by_sandbox_id(sandbox_id="sbx-1")

        assert result is not None
        assert result.id == 1

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_binding_by_sandbox_id(sandbox_id="nonexistent")

        assert result is None


class TestGetBindingBySandboxIdLike:
    def test_found(self, repository, mock_session):
        model = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repository.get_binding_by_sandbox_id_like(
            sandbox_id_prefix="sbx-prefix"
        )

        assert result is not None
        assert result.id == 1

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repository.get_binding_by_sandbox_id_like(
            sandbox_id_prefix="nonexistent"
        )

        assert result is None


class TestListByDeviceId:
    def test_returns_list(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        model2 = _make_binding_model(id_val=2)

        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repository.list_by_device_id(device_id="dev-1")

        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 2

    def test_with_env_filter(self, repository, mock_session):
        model1 = _make_binding_model(id_val=1)
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1
        ]

        result = repository.list_by_device_id(device_id="dev-1", env="prod")

        assert len(result) == 1

    def test_empty(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repository.list_by_device_id(device_id="dev-1")

        assert result == []


class TestUpdateDevicePropsTtlByPaasDeviceId:
    def test_update(self, repository, mock_session):
        repository.update_device_props_ttl_by_paas_device_id(
            paas_device_id="sbx-1",
            ttl_expiration_timestamp=1234567890,
            ttl_expiration_time="2026-01-01 00:00:00",
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        assert call_args[1]["synchronize_session"] is False
        assert "device_props" in call_args[0][0]


class TestUpdateDevicePropsRefreshFailCount:
    def test_update(self, repository, mock_session):
        repository.update_device_props_refresh_fail_count(
            binding_id=1, refresh_fail_count=3
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_args = mock_session.query.return_value.filter.return_value.update.call_args
        assert call_args[1]["synchronize_session"] is False
        assert "device_props" in call_args[0][0]


class TestListActiveSandboxesWithBot:
    def test_returns_items(self, repository, mock_session):
        # Two execute calls: count_sql (scalar) and data_sql (fetchall)
        count_result = _make_execute_result(scalar_val=1)
        row_data = [
            1,  # id
            "ent-1",  # entity_id
            "bot",  # entity_type
            "dev-1",  # device_id
            "arca",  # device_provider
            "prod",  # env
            '{"sandbox_id": "sbx1"}',  # device_props
            "ACTIVE",  # status
            "test",  # apply_reason
            "user1",  # applied_by
            None,  # release_reason
            None,  # released_by
            None,  # released_at
            None,  # last_alive_at
            datetime.now(),  # gmt_create
            datetime.now(),  # gmt_modified
            "bot-1",  # bot_id
            "MyBot",  # bot_name
            "ACTIVE",  # bot_status
            "engine1",  # active_engine
            datetime.now(),  # bot_gmt_create
            datetime.now(),  # bot_gmt_modified
        ]
        data_result = _make_execute_result(rows=[row_data])

        mock_session.execute.side_effect = [count_result, data_result]

        total, items = repository.list_active_sandboxes_with_bot(
            page=1, page_size=20, env="prod"
        )

        assert total == 1
        assert len(items) == 1
        record, bot_info = items[0]
        assert record.id == 1
        assert record.entity_id == "ent-1"
        assert record.device_props == {"sandbox_id": "sbx1"}
        assert bot_info["bot_id"] == "bot-1"
        assert bot_info["bot_name"] == "MyBot"

    def test_empty_results(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        total, items = repository.list_active_sandboxes_with_bot()

        assert total == 0
        assert items == []

    def test_invalid_json_device_props(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=1)
        row_data = [
            1,
            "ent-1",
            "bot",
            "dev-1",
            "arca",
            "prod",
            "invalid-json",  # device_props
            "ACTIVE",
            "test",
            "user1",
            None,
            None,
            None,
            None,
            datetime.now(),
            datetime.now(),
            "bot-1",
            "MyBot",
            "ACTIVE",
            "engine1",
            datetime.now(),
            datetime.now(),
        ]
        data_result = _make_execute_result(rows=[row_data])

        mock_session.execute.side_effect = [count_result, data_result]

        total, items = repository.list_active_sandboxes_with_bot()

        assert total == 1
        assert len(items) == 1
        record, _ = items[0]
        assert record.device_props == {}

    def test_invalid_sort_by_defaults_to_id(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        repository.list_active_sandboxes_with_bot(sort_by="invalid_field")

        # The count SQL call should have been made
        assert mock_session.execute.call_count == 2

    def test_with_device_provider(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        repository.list_active_sandboxes_with_bot(env="prod", device_provider="arca")

        assert mock_session.execute.call_count == 2

    def test_sort_order_asc(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        repository.list_active_sandboxes_with_bot(sort_order="asc")

        assert mock_session.execute.call_count == 2


class TestListAllActiveBotDevice:
    def test_returns_items(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=1)
        row_data = ["bot-1", "ent-1", 1, "personal", "engine1", "ACTIVE"]
        data_result = _make_execute_result(rows=[row_data])

        mock_session.execute.side_effect = [count_result, data_result]

        total, items = repository.list_all_active_bot_device(
            page=1, page_size=20, env="prod"
        )

        assert total == 1
        assert len(items) == 1
        assert items[0]["bot_id"] == "bot-1"
        assert items[0]["entity_id"] == "ent-1"
        assert items[0]["binding_id"] == 1
        assert items[0]["bot_type"] == "personal"

    def test_empty(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        total, items = repository.list_all_active_bot_device()

        assert total == 0
        assert items == []

    def test_with_bot_type(self, repository, mock_session):
        count_result = _make_execute_result(scalar_val=0)
        data_result = _make_execute_result(rows=[])

        mock_session.execute.side_effect = [count_result, data_result]

        repository.list_all_active_bot_device(bot_type="service")

        assert mock_session.execute.call_count == 2


class TestGetBotBinding:
    def test_found(self, repository, mock_session):
        row = ["bot-1", "ent-1", 1, "personal", "engine1", "ACTIVE", "arca"]
        result_mock = MagicMock()
        result_mock.fetchone.return_value = row
        result_mock.scalar.return_value = None
        mock_session.execute.return_value = result_mock

        result = repository.get_bot_binding(
            bot_id="bot-1", entity_id="ent-1", env="prod"
        )

        assert result is not None
        assert result["bot_id"] == "bot-1"
        assert result["entity_id"] == "ent-1"
        assert result["binding_id"] == 1
        assert result["bot_type"] == "personal"
        assert result["device_provider"] == "arca"

    def test_not_found(self, repository, mock_session):
        result_mock = MagicMock()
        result_mock.fetchone.return_value = None
        mock_session.execute.return_value = result_mock

        result = repository.get_bot_binding(bot_id="bot-1", entity_id="ent-1")

        assert result is None


class TestUpdateBaasDeviceTtl:
    def test_update(self, repository, mock_session):
        repository.update_baas_device_ttl(
            device_uuid="uuid-1",
            ttl_expiration_time="2026-01-01 00:00:00",
            ttl_expiration_timestamp=1234567890,
        )

        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["device_uuid"] == "uuid-1"
        assert params["ttl_expiration_time"] == "2026-01-01 00:00:00"
        assert params["ttl_expiration_timestamp"] == 1234567890
