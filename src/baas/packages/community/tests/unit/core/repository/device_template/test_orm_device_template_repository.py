"""
OrmDeviceTemplateRepository unit tests.

Uses pytest + MagicMock session-mock pattern matching test_orm_tenant_repository.py.
Covers all 10 protocol methods via OrmDeviceTemplateRepository with
@with_orm_session decorator and DeviceTemplateModel.to_record().
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from secbaas.core.repository.device_template import (
    DeviceTemplateRecord,
    OrmDeviceTemplateRepository,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    session = MagicMock()
    return session


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session via orm_session()."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture
def repository(mock_database):
    """Create an OrmDeviceTemplateRepository backed by the mock database."""
    return OrmDeviceTemplateRepository(mock_database)


# ==================== Constants ====================

NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


# ==================== Model builder ====================


def _make_mock_model(**overrides):
    """Build a MagicMock DeviceTemplateModel with default attributes."""
    defaults = {
        "id": 1,
        "gmt_create": NOW,
        "gmt_modified": NOW,
        "template_uuid": "tpl-001",
        "tenant": "my_tenant",
        "is_deleted": 0,
        "creator": "creator-001",
        "modifier": "modifier-001",
        "status": "CREATED",
        "name": "Test Template",
        "description": "A test template",
        "config": '{"key": "value"}',
        "template_id": 100,
        "type": "Local",
    }
    defaults.update(overrides)
    model = MagicMock()
    model.configure_mock(**defaults)
    model.to_record.return_value = DeviceTemplateRecord(
        id=model.id,
        gmt_create=model.gmt_create,
        gmt_modified=model.gmt_modified,
        template_uuid=model.template_uuid,
        tenant=model.tenant,
        is_deleted=model.is_deleted or 0,
        creator=model.creator,
        modifier=model.modifier,
        status=model.status,
        name=model.name,
        description=model.description,
        config=(
            json.loads(model.config) if isinstance(model.config, str) else model.config
        )
        or {},
        template_id=model.template_id,
        type=model.type,
    )
    return model


def _setup_query_chain(mock_session, *results):
    """Set up session.query().filter().<chain>() to return successive results."""
    query = MagicMock()
    filter_result = MagicMock()

    chain = filter_result
    for attr, value in results:
        setattr(chain, attr, MagicMock(return_value=value))

    query.filter.return_value = filter_result
    mock_session.query.return_value = query
    return query, filter_result


# ==================== DeviceTemplateRecord dataclass ====================


class TestDeviceTemplateRecord:
    """Tests for the DeviceTemplateRecord dataclass."""

    def test_create_record(self):
        record = DeviceTemplateRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            template_uuid="tpl-001",
            tenant="my_tenant",
            is_deleted=0,
            creator="creator-1",
            modifier="modifier-1",
            status="ONLINE",
            name="Test",
            description="Desc",
            config={"key": "val"},
            template_id=100,
            type="Local",
        )
        assert record.id == 1
        assert record.template_uuid == "tpl-001"
        assert record.status == "ONLINE"
        assert record.config == {"key": "val"}
        assert record.description == "Desc"

    def test_record_none_description(self):
        record = DeviceTemplateRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            template_uuid="t",
            tenant="t",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="s",
            name="n",
            description=None,
            config={},
            template_id=0,
            type="L",
        )
        assert record.description is None
        assert record.config == {}

    def test_record_uses_slots(self):
        record = DeviceTemplateRecord(
            id=1,
            gmt_create=NOW,
            gmt_modified=NOW,
            template_uuid="t",
            tenant="t",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="s",
            name="n",
            description=None,
            config={},
            template_id=0,
            type="L",
        )
        with pytest.raises(AttributeError):
            _ = record.__dict__


# ==================== Constructor ====================


class TestConstructor:
    """Tests for OrmDeviceTemplateRepository.__init__."""

    def test_constructor_sets_database(self, mock_database):
        repo = OrmDeviceTemplateRepository(mock_database)
        assert repo._database is mock_database

    def test_constructor_stores_attribute(self, mock_database):
        repo = OrmDeviceTemplateRepository(mock_database)
        assert hasattr(repo, "_database")


# ==================== insert_template ====================


class TestInsertTemplate:
    """Tests for OrmDeviceTemplateRepository.insert_template()."""

    @pytest.fixture(autouse=True)
    def _patch_model(self):
        """Patch DeviceTemplateModel so constructor returns a MagicMock capturing kwargs."""
        with patch(
            "secbaas.core.repository.device_template._orm_repository.DeviceTemplateModel",
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
        result = repository.insert_template(
            template_uuid="tpl-001",
            template_id=100,
            type="Local",
            tenant="my_tenant",
            creator="creator-1",
            modifier="modifier-1",
            status="CREATED",
            name="Test Template",
            description="A test",
            config={"key": "value"},
        )

        assert result == 999
        _patch_model.assert_called_once()
        kwargs = _patch_model.call_args[1]
        assert kwargs["template_uuid"] == "tpl-001"
        assert kwargs["template_id"] == 100
        assert kwargs["type"] == "Local"
        assert kwargs["tenant"] == "my_tenant"
        assert kwargs["creator"] == "creator-1"
        assert kwargs["modifier"] == "modifier-1"
        assert kwargs["status"] == "CREATED"
        assert kwargs["name"] == "Test Template"
        assert kwargs["description"] == "A test"
        assert kwargs["config"] == json.dumps({"key": "value"}, ensure_ascii=False)

    def test_insert_with_none_config(self, repository, mock_session, _patch_model):
        result = repository.insert_template(
            template_uuid="tpl-002",
            template_id=200,
            type="Remote",
            tenant="t2",
            creator="c2",
            modifier="m2",
            status="CREATED",
            name="No Config Template",
            description=None,
            config=None,
        )

        assert result == 999
        kwargs = _patch_model.call_args[1]
        assert kwargs["config"] is None

    def test_insert_with_default_status(self, repository, mock_session, _patch_model):
        result = repository.insert_template(
            template_uuid="tpl-003",
            template_id=300,
            type="Local",
            tenant="t3",
            creator="c3",
            modifier="m3",
            name="Default Status",
        )

        assert result == 999
        kwargs = _patch_model.call_args[1]
        assert kwargs["status"] == "CREATED"

    def test_insert_with_none_description(self, repository, mock_session, _patch_model):
        result = repository.insert_template(
            template_uuid="tpl-004",
            template_id=400,
            type="Local",
            tenant="t4",
            creator="c4",
            modifier="m4",
            name="No Description",
            description=None,
        )

        assert result == 999
        kwargs = _patch_model.call_args[1]
        assert kwargs["description"] is None

    def test_insert_adds_and_flushes(self, repository, mock_session, _patch_model):
        repository.insert_template(
            template_uuid="tpl-005",
            template_id=500,
            type="Local",
            tenant="t5",
            creator="c5",
            modifier="m5",
            name="Template 5",
        )

        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()


# ==================== get_by_id ====================


class TestGetById:
    """Tests for OrmDeviceTemplateRepository.get_by_id()."""

    def test_found(self, repository, mock_session):
        row = _make_mock_model(id=1, template_uuid="tpl-001")
        query, _ = _setup_query_chain(mock_session, ("first", row))

        result = repository.get_by_id(1, "my_tenant")

        assert result is not None
        assert isinstance(result, DeviceTemplateRecord)
        assert result.id == 1
        assert result.template_uuid == "tpl-001"
        mock_session.query.assert_called_once()

    def test_not_found(self, repository, mock_session):
        _setup_query_chain(mock_session, ("first", None))

        result = repository.get_by_id(999, "my_tenant")

        assert result is None

    def test_with_different_tenant(self, repository, mock_session):
        row = _make_mock_model(id=2, tenant="tenant_B")
        _setup_query_chain(mock_session, ("first", row))

        result = repository.get_by_id(2, "tenant_B")

        assert result is not None
        assert result.tenant == "tenant_B"


# ==================== get_by_template_id ====================


class TestGetByTemplateId:
    """Tests for OrmDeviceTemplateRepository.get_by_template_id()."""

    def test_found(self, repository, mock_session):
        row = _make_mock_model(id=3, template_id=300, template_uuid="tpl-300")
        _setup_query_chain(mock_session, ("first", row))

        result = repository.get_by_template_id(300)

        assert result is not None
        assert result.template_id == 300
        assert result.template_uuid == "tpl-300"

    def test_not_found(self, repository, mock_session):
        _setup_query_chain(mock_session, ("first", None))

        result = repository.get_by_template_id(99999)

        assert result is None


# ==================== get_by_template_uuid ====================


class TestGetByTemplateUuid:
    """Tests for OrmDeviceTemplateRepository.get_by_template_uuid()."""

    def test_found(self, repository, mock_session):
        row = _make_mock_model(
            id=4, template_uuid="tpl-abc", tenant="t1", status="ONLINE"
        )
        _setup_query_chain(mock_session, ("first", row))

        result = repository.get_by_template_uuid("tpl-abc", "t1", "ONLINE")

        assert result is not None
        assert result.template_uuid == "tpl-abc"
        assert result.status == "ONLINE"

    def test_not_found(self, repository, mock_session):
        _setup_query_chain(mock_session, ("first", None))

        result = repository.get_by_template_uuid("nonexistent", "t1", "ONLINE")

        assert result is None

    def test_with_different_status(self, repository, mock_session):
        row = _make_mock_model(
            id=5, template_uuid="tpl-def", tenant="t2", status="OFFLINE"
        )
        _setup_query_chain(mock_session, ("first", row))

        result = repository.get_by_template_uuid("tpl-def", "t2", "OFFLINE")

        assert result is not None
        assert result.status == "OFFLINE"


# ==================== list_by_template_uuid ====================


class TestListByTemplateUuid:
    """Tests for OrmDeviceTemplateRepository.list_by_template_uuid()."""

    def test_returns_multiple_records(self, repository, mock_session):
        rows = [
            _make_mock_model(id=10, template_uuid="tpl-xyz", status="ONLINE"),
            _make_mock_model(id=9, template_uuid="tpl-xyz", status="CREATED"),
            _make_mock_model(id=8, template_uuid="tpl-xyz", status="OFFLINE"),
        ]
        query = MagicMock()
        filter_result = MagicMock()
        order_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.order_by.return_value = order_result
        order_result.all.return_value = rows
        mock_session.query.return_value = query

        result = repository.list_by_template_uuid("tpl-xyz", "my_tenant")

        assert len(result) == 3
        assert result[0].id == 10
        assert result[1].id == 9
        assert result[2].id == 8

    def test_returns_empty_list(self, repository, mock_session):
        _setup_query_chain(mock_session, ("all", []))

        result = repository.list_by_template_uuid("nonexistent", "my_tenant")

        assert result == []


# ==================== get_online_by_template_uuid ====================


class TestGetOnlineByTemplateUuid:
    """Tests for OrmDeviceTemplateRepository.get_online_by_template_uuid()."""

    def test_found_online(self, repository, mock_session):
        row = _make_mock_model(id=10, template_uuid="tpl-online", status="ONLINE")
        _setup_query_chain(mock_session, ("first", row))

        result = repository.get_online_by_template_uuid("tpl-online", "my_tenant")

        assert result is not None
        assert result.status == "ONLINE"
        assert result.template_uuid == "tpl-online"

    def test_not_found(self, repository, mock_session):
        _setup_query_chain(mock_session, ("first", None))

        result = repository.get_online_by_template_uuid("no-online", "my_tenant")

        assert result is None


# ==================== update_template ====================


class TestUpdateTemplate:
    """Tests for OrmDeviceTemplateRepository.update_template()."""

    def test_update_name_only(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        result = repository.update_template(
            template_uuid="tpl-001",
            tenant="my_tenant",
            status="CREATED",
            modifier="modifier-upd",
            name="Updated Name",
        )

        assert result == 1
        filter_result.update.assert_called_once()

    def test_update_all_fields(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        result = repository.update_template(
            template_uuid="tpl-002",
            tenant="t2",
            status="ONLINE",
            modifier="m-new",
            name="New Name",
            description="New Desc",
            config={"updated": True},
        )

        assert result == 1
        call_kwargs = filter_result.update.call_args[0][0]
        assert call_kwargs["name"] == "New Name"
        assert call_kwargs["description"] == "New Desc"
        assert "config" in call_kwargs

    def test_update_no_optional_fields(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        result = repository.update_template(
            template_uuid="tpl-003",
            tenant="t3",
            status="CREATED",
            modifier="m3",
        )

        assert result == 1
        filter_result.update.assert_called_once()

    def test_update_affected_rows_zero(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 0
        mock_session.query.return_value = query

        result = repository.update_template(
            template_uuid="tpl-missing",
            tenant="t",
            status="CREATED",
            modifier="m",
            name="Wont Work",
        )

        assert result == 0


# ==================== update_status ====================


class TestUpdateStatus:
    """Tests for OrmDeviceTemplateRepository.update_status()."""

    def test_successful_status_update(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        repository.update_status(
            template_uuid="tpl-001",
            tenant="my_tenant",
            current_status="CREATED",
            new_status="ONLINE",
        )

        filter_result.update.assert_called_once()
        call_kwargs = filter_result.update.call_args[0][0]
        assert call_kwargs["status"] == "ONLINE"
        assert "gmt_modified" in call_kwargs

    def test_status_transition_offline_to_online(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        repository.update_status(
            template_uuid="tpl-002",
            tenant="t2",
            current_status="OFFLINE",
            new_status="ONLINE",
        )

        call_kwargs = filter_result.update.call_args[0][0]
        assert call_kwargs["status"] == "ONLINE"


# ==================== soft_delete ====================


class TestSoftDelete:
    """Tests for OrmDeviceTemplateRepository.soft_delete()."""

    def test_successful_soft_delete(self, repository, mock_session):
        row = _make_mock_model(id=55, template_uuid="tpl-001", status="CREATED")
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.first.return_value = row
        filter_result.update.return_value = 1
        mock_session.query.return_value = query

        repository.soft_delete(
            template_uuid="tpl-001",
            tenant="my_tenant",
            status="CREATED",
            modifier="deleter",
        )

        assert filter_result.first.call_count == 1
        assert filter_result.update.call_count == 1
        call_kwargs = filter_result.update.call_args[0][0]
        assert call_kwargs["is_deleted"] == 55
        assert call_kwargs["modifier"] == "deleter"
        assert "gmt_modified" in call_kwargs

    def test_not_found_returns_early(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.first.return_value = None
        mock_session.query.return_value = query

        repository.soft_delete(
            template_uuid="nonexistent",
            tenant="my_tenant",
            status="CREATED",
            modifier="deleter",
        )

        assert filter_result.first.call_count == 1
        filter_result.update.assert_not_called()


# ==================== list_templates ====================


class TestListTemplates:
    """Tests for OrmDeviceTemplateRepository.list_templates()."""

    def test_list_all_templates(self, repository, mock_session):
        rows = [
            _make_mock_model(id=10, template_uuid="tpl-10"),
            _make_mock_model(id=9, template_uuid="tpl-9"),
        ]

        # Build the query chain
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter

        # .with_entities().scalar() for count
        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 25

        # .order_by().offset().limit().all() for rows
        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = rows

        mock_session.query.return_value = query

        total, items = repository.list_templates(
            tenant="my_tenant",
            page=1,
            page_size=20,
        )

        assert total == 25
        assert len(items) == 2
        assert items[0].id == 10
        assert items[1].id == 9

    def test_list_with_status_filter(self, repository, mock_session):
        row = _make_mock_model(id=1, template_uuid="tpl-001", status="ONLINE")

        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter

        # After status filter
        status_filter = MagicMock()
        base_filter.filter.return_value = status_filter

        with_entities_mock = MagicMock()
        status_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 5

        order_mock = MagicMock()
        status_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = [row]

        mock_session.query.return_value = query

        total, items = repository.list_templates(
            tenant="my_tenant",
            status="ONLINE",
            page=1,
            page_size=10,
        )

        assert total == 5
        assert len(items) == 1
        assert items[0].status == "ONLINE"

    def test_pagination_page_3(self, repository, mock_session):
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter

        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 55

        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = [_make_mock_model(id=41)]

        mock_session.query.return_value = query

        total, items = repository.list_templates(
            tenant="my_tenant",
            page=3,
            page_size=20,
        )

        assert total == 55
        assert len(items) == 1
        # offset = (3-1)*20 = 40
        order_mock.offset.assert_called_with(40)

    def test_empty_results(self, repository, mock_session):
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter

        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 0

        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = []

        mock_session.query.return_value = query

        total, items = repository.list_templates(tenant="my_tenant")

        assert total == 0
        assert items == []

    def test_default_page_and_size(self, repository, mock_session):
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter

        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 3

        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = []

        mock_session.query.return_value = query

        repository.list_templates(tenant="my_tenant")

        # offset = (1-1)*20 = 0
        order_mock.offset.assert_called_with(0)


# ==================== get_default_local_template_id ====================


class TestGetDefaultLocalTemplateId:
    """Tests for OrmDeviceTemplateRepository.get_default_local_template_id()."""

    def test_found_returns_template_id(self, repository, mock_session):
        row = MagicMock()
        row.template_id = 100
        query = MagicMock()
        filter_result = MagicMock()
        order_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.order_by.return_value = order_result
        order_result.first.return_value = row
        mock_session.query.return_value = query

        result = repository.get_default_local_template_id()

        assert result == 100

    def test_not_found_returns_none(self, repository, mock_session):
        query = MagicMock()
        filter_result = MagicMock()
        order_result = MagicMock()
        query.filter.return_value = filter_result
        filter_result.order_by.return_value = order_result
        order_result.first.return_value = None
        mock_session.query.return_value = query

        result = repository.get_default_local_template_id()

        assert result is None


# ==================== @with_orm_session lifecycle ====================


class TestWithOrmSessionLifecycle:
    """Tests verifying the @with_orm_session decorator lifecycle."""

    def test_decorator_opens_and_closes_session(self, mock_database, mock_session):
        repo = OrmDeviceTemplateRepository(mock_database)
        row = _make_mock_model(id=1)
        _setup_query_chain(mock_session, ("first", row))

        repo.get_by_id(1, "my_tenant")

        mock_database.orm_session.assert_called_once()
        assert mock_session.query.called

    def test_session_cleaned_up_after_method(self, mock_database, mock_session):
        repo = OrmDeviceTemplateRepository(mock_database)
        row = _make_mock_model(id=1)
        _setup_query_chain(mock_session, ("first", row))

        repo.get_by_id(1, "my_tenant")

        session_ctx = mock_database.orm_session.return_value
        session_ctx.__enter__.assert_called_once()
        session_ctx.__exit__.assert_called_once()

    def test_multiple_method_calls(self, mock_database, mock_session):
        repo = OrmDeviceTemplateRepository(mock_database)

        # First call: insert
        with patch(
            "secbaas.core.repository.device_template._orm_repository.DeviceTemplateModel",
        ) as mock_cls:

            def _side_effect(**kwargs):
                mock_instance = MagicMock()
                mock_instance.id = 1
                for key, value in kwargs.items():
                    setattr(mock_instance, key, value)
                return mock_instance

            mock_cls.side_effect = _side_effect

            repo.insert_template(
                template_uuid="t1",
                template_id=1,
                type="Local",
                tenant="t",
                creator="c",
                modifier="m",
                name="n",
            )

        # Second call: get_by_id
        row = _make_mock_model(id=1)
        _setup_query_chain(mock_session, ("first", row))
        repo.get_by_id(1, "t")

        # orm_session called twice (once per decorated method)
        assert mock_database.orm_session.call_count == 2


# ==================== Method round-trip tests ====================


class TestMethodRoundTrips:
    """Tests covering multiple methods in sequence on the same repository."""

    @pytest.fixture(autouse=True)
    def _patch_model(self):
        """Patch DeviceTemplateModel for insert operations."""
        with patch(
            "secbaas.core.repository.device_template._orm_repository.DeviceTemplateModel",
        ) as mock_cls:

            def _side_effect(**kwargs):
                mock_instance = MagicMock()
                mock_instance.id = 42
                for key, value in kwargs.items():
                    setattr(mock_instance, key, value)
                return mock_instance

            mock_cls.side_effect = _side_effect
            yield mock_cls

    def test_insert_then_get_by_id(self, repository, mock_session, _patch_model):
        repo_id = repository.insert_template(
            template_uuid="tpl-42",
            template_id=420,
            type="Local",
            tenant="t",
            creator="c",
            modifier="m",
            name="T42",
        )
        assert repo_id == 42

        row = _make_mock_model(id=42, template_uuid="tpl-42")
        _setup_query_chain(mock_session, ("first", row))
        result = repository.get_by_id(42, "t")
        assert result is not None
        assert result.template_uuid == "tpl-42"

    def test_insert_then_list(self, repository, mock_session, _patch_model):
        repository.insert_template(
            template_uuid="tpl-1",
            template_id=1,
            type="Local",
            tenant="t",
            creator="c",
            modifier="m",
            name="T1",
        )

        # Setup list_templates mock chain
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter
        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 1
        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = [_make_mock_model(id=1, template_uuid="tpl-1")]
        mock_session.query.return_value = query

        total, items = repository.list_templates(tenant="t")
        assert total == 1
        assert len(items) == 1

    def test_full_lifecycle(self, repository, mock_session, _patch_model):
        """Insert → get → list → update → update_status → soft_delete."""
        # Insert
        new_id = repository.insert_template(
            template_uuid="tpl-lifecycle",
            template_id=1000,
            type="Local",
            tenant="t",
            creator="c",
            modifier="m",
            name="Lifecycle Tpl",
        )
        assert new_id == 42

        # GetById
        row = _make_mock_model(
            id=42, template_uuid="tpl-lifecycle", status="CREATED", name="Lifecycle Tpl"
        )
        _setup_query_chain(mock_session, ("first", row))
        record = repository.get_by_id(42, "t")
        assert record is not None
        assert record.status == "CREATED"

        # ListTemplates
        query = MagicMock()
        base_filter = MagicMock()
        query.filter.return_value = base_filter
        with_entities_mock = MagicMock()
        base_filter.with_entities.return_value = with_entities_mock
        with_entities_mock.scalar.return_value = 1
        order_mock = MagicMock()
        base_filter.order_by.return_value = order_mock
        offset_mock = MagicMock()
        order_mock.offset.return_value = offset_mock
        limit_mock = MagicMock()
        offset_mock.limit.return_value = limit_mock
        limit_mock.all.return_value = [_make_mock_model(id=42)]
        mock_session.query.return_value = query
        total, items = repository.list_templates(tenant="t")
        assert total == 1

        # Update
        upd_query = MagicMock()
        upd_filter = MagicMock()
        upd_query.filter.return_value = upd_filter
        upd_filter.update.return_value = 1
        mock_session.query.return_value = upd_query
        result = repository.update_template(
            template_uuid="tpl-lifecycle",
            tenant="t",
            status="CREATED",
            modifier="m",
            name="Updated Lifecycle",
        )
        assert result == 1

        # UpdateStatus
        status_query = MagicMock()
        status_filter = MagicMock()
        status_query.filter.return_value = status_filter
        status_filter.update.return_value = 1
        mock_session.query.return_value = status_query
        repository.update_status(
            template_uuid="tpl-lifecycle",
            tenant="t",
            current_status="CREATED",
            new_status="ONLINE",
        )

        # SoftDelete
        soft_row = _make_mock_model(
            id=42, template_uuid="tpl-lifecycle", status="ONLINE"
        )
        soft_query = MagicMock()
        soft_filter = MagicMock()
        soft_query.filter.return_value = soft_filter
        soft_filter.first.return_value = soft_row
        soft_filter.update.return_value = 1
        mock_session.query.return_value = soft_query
        repository.soft_delete(
            template_uuid="tpl-lifecycle",
            tenant="t",
            status="ONLINE",
            modifier="deleter",
        )

        assert soft_filter.first.call_count == 1
        assert soft_filter.update.call_count == 1
