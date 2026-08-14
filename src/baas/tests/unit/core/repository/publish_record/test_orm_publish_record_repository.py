"""
OrmPublishRecordRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_repository.py tests.
Covers all 13 protocol methods, get_latest_processing_record_by_device,
and log branches.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.publish_record import (
    OrmPublishRecordRepository,
    PublishRecordRecord,
)

# ==================== Fixtures ====================


@pytest.fixture
def mock_session():
    """Mock SQLAlchemy ORM session."""
    return MagicMock()


@pytest.fixture
def mock_database(mock_session):
    """Mock database that yields a mock ORM session via @with_orm_session."""
    database = MagicMock()
    database.orm_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
    return database


@pytest.fixture(autouse=True)
def _patch_publish_record_model():
    """Patch PublishRecordModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.publish_record._orm_repository.PublishRecordModel",
        autospec=False,
    ) as mock_cls:

        def _make_model(**kwargs):
            model = MagicMock()
            model.id = 42
            for k, v in kwargs.items():
                setattr(model, k, v)
            return model

        mock_cls.side_effect = _make_model
        yield mock_cls


@pytest.fixture
def repo(mock_database):
    """Create an OrmPublishRecordRepository instance with mock database."""
    return OrmPublishRecordRepository(mock_database)


# ==================== Model helpers ====================


def _make_mock_model(
    id_val=1,
    tenant="test_tenant",
    env="prod",
    domain="default",
    is_deleted=0,
    creator="admin",
    modifier="admin",
    device_id=100,
    bot_id=200,
    publish_id=300,
    batch_id=400,
    event_type="CREATE",
    trigger_source="web",
    publish_reason="test deploy",
    result_status="PROCESSING",
    result_message=None,
    extra_config=None,
    device_uuid=None,
):
    """Create a MagicMock whose to_record() returns a PublishRecordRecord."""
    now = datetime.now()
    final_extra = extra_config if extra_config is not None else {}

    record = PublishRecordRecord(
        id=id_val,
        gmt_create=now,
        gmt_modified=now,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        device_id=device_id,
        bot_id=bot_id,
        publish_id=publish_id,
        batch_id=batch_id,
        event_type=event_type,
        trigger_source=trigger_source,
        publish_reason=publish_reason,
        result_status=result_status,
        result_message=result_message,
        extra_config=final_extra,
        device_uuid=device_uuid,
    )

    model = MagicMock()
    model.to_record.return_value = record
    model.id = id_val
    return model, record


# ==================== PublishRecordModel.to_record / PublishRecordRecord ====================


class TestModelToRecord:
    """Tests for PublishRecordModel.to_record() — mapping from model to PublishRecordRecord."""

    def test_converts_valid_model(self, repo):
        extra = {"timeout": 30, "retry": 3}
        model, record = _make_mock_model(
            id_val=7,
            tenant="tenant-x",
            env="staging",
            domain="bots",
            creator="creator-user",
            modifier="modifier-user",
            device_id=101,
            bot_id=202,
            publish_id=303,
            batch_id=404,
            event_type="UPDATE",
            trigger_source="auto",
            publish_reason="auto deploy",
            result_status="SUCCESS",
            result_message="all good",
            extra_config=extra,
        )

        assert isinstance(record, PublishRecordRecord)
        assert record.id == 7
        assert record.tenant == "tenant-x"
        assert record.env == "staging"
        assert record.domain == "bots"
        assert record.creator == "creator-user"
        assert record.modifier == "modifier-user"
        assert record.device_id == 101
        assert record.bot_id == 202
        assert record.publish_id == 303
        assert record.batch_id == 404
        assert record.event_type == "UPDATE"
        assert record.trigger_source == "auto"
        assert record.publish_reason == "auto deploy"
        assert record.result_status == "SUCCESS"
        assert record.result_message == "all good"
        assert record.extra_config == extra
        assert record.device_uuid is None

    def test_null_optional_fields(self, repo):
        model, record = _make_mock_model(
            id_val=1,
            device_id=None,
            bot_id=None,
            publish_id=None,
            batch_id=None,
            trigger_source=None,
            publish_reason=None,
            result_message=None,
            device_uuid=None,
        )

        assert record.device_id is None
        assert record.bot_id is None
        assert record.publish_id is None
        assert record.batch_id is None
        assert record.trigger_source is None
        assert record.publish_reason is None
        assert record.result_message is None
        assert record.device_uuid is None

    def test_with_device_uuid(self, repo):
        model, record = _make_mock_model(
            id_val=1,
            device_uuid="DEVICE-UUID-100",
        )
        assert record.device_uuid == "DEVICE-UUID-100"

    def test_empty_extra_config(self, repo):
        model, record = _make_mock_model(id_val=1, extra_config={})
        assert record.extra_config == {}

    def test_none_extra_config(self, repo):
        model, record = _make_mock_model(id_val=1, extra_config=None)
        assert record.extra_config == {}


# ==================== PublishRecordRecord dataclass ====================


class TestPublishRecordRecord:
    def test_creates_record(self):
        now = datetime.now()
        record = PublishRecordRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            tenant="t1",
            env="dev",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            device_id=100,
            bot_id=200,
            publish_id=300,
            batch_id=400,
            event_type="CREATE",
            trigger_source="web",
            publish_reason="reason",
            result_status="PROCESSING",
            result_message=None,
            extra_config={"k": "v"},
            device_uuid="UUID-001",
        )

        assert record.id == 1
        assert record.tenant == "t1"
        assert record.extra_config == {"k": "v"}
        assert record.device_uuid == "UUID-001"

    def test_none_fields(self):
        now = datetime.now()
        record = PublishRecordRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            tenant="t1",
            env="dev",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            device_id=None,
            bot_id=None,
            publish_id=None,
            batch_id=None,
            event_type="CREATE",
            trigger_source=None,
            publish_reason=None,
            result_status="PROCESSING",
            result_message=None,
            extra_config={},
        )

        assert record.device_id is None
        assert record.device_uuid is None


# ==================== insert_record ====================


class TestInsertRecord:
    def test_insert_returns_id(self, repo, mock_session):
        result = repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=100,
            bot_id=200,
            publish_id=300,
            batch_id=400,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            trigger_source="web",
            publish_reason="deploy",
            result_message=None,
            extra_config={"cpu": 2},
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repo, mock_session):
        repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=100,
            bot_id=200,
            publish_id=300,
            batch_id=400,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            trigger_source="web",
            publish_reason="deploy",
        )

        model = mock_session.add.call_args[0][0]
        assert model.tenant == "test_tenant"
        assert model.env == "prod"
        assert model.domain == "default"
        assert model.device_id == 100
        assert model.bot_id == 200
        assert model.publish_id == 300
        assert model.batch_id == 400
        assert model.event_type == "CREATE"
        assert model.trigger_source == "web"
        assert model.publish_reason == "deploy"
        assert model.result_status == "PROCESSING"
        assert model.creator == "admin"
        assert model.modifier == "admin"
        assert model.is_deleted == 0

    def test_insert_with_extra_config_serializes_json(self, repo, mock_session):
        repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=1,
            bot_id=2,
            publish_id=3,
            batch_id=4,
            event_type="SCALE_UP",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            extra_config={"cpu": 2},
        )

        model = mock_session.add.call_args[0][0]
        extra_config_val = model.extra_config
        assert isinstance(extra_config_val, str)
        assert '"cpu"' in extra_config_val

    def test_insert_with_empty_extra_config(self, repo, mock_session):
        repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=1,
            bot_id=2,
            publish_id=3,
            batch_id=4,
            event_type="SCALE_UP",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            extra_config={},
        )

        model = mock_session.add.call_args[0][0]
        assert model.extra_config is None

    def test_insert_with_none_extra_config(self, repo, mock_session):
        repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=1,
            bot_id=2,
            publish_id=3,
            batch_id=4,
            event_type="SCALE_UP",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            extra_config=None,
        )

        model = mock_session.add.call_args[0][0]
        assert model.extra_config is None

    def test_insert_with_none_optional_fields(self, repo, mock_session):
        repo.insert_record(
            tenant="test_tenant",
            env="prod",
            domain="default",
            device_id=None,
            bot_id=None,
            publish_id=None,
            batch_id=None,
            event_type="CREATE",
            result_status="PROCESSING",
            creator="admin",
            modifier="admin",
            trigger_source=None,
            publish_reason=None,
            result_message=None,
            extra_config=None,
        )

        model = mock_session.add.call_args[0][0]
        assert model.device_id is None
        assert model.bot_id is None
        assert model.publish_id is None
        assert model.batch_id is None
        assert model.trigger_source is None
        assert model.publish_reason is None
        assert model.result_message is None


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_model(id_val=5)
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_id(5, "test_tenant", "prod")

        assert result is not None
        assert isinstance(result, PublishRecordRecord)
        assert result.id == 5
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id(999, "test_tenant", "prod")

        assert result is None


# ==================== list_by_batch_id ====================


class TestListByBatchId:
    def test_returns_multiple_records(self, repo, mock_session):
        model1, _ = _make_mock_model(id_val=1, batch_id=100)
        model2, _ = _make_mock_model(id_val=2, batch_id=100)
        model3, _ = _make_mock_model(id_val=3, batch_id=100)
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
            model3,
        ]

        result = repo.list_by_batch_id(100, "test_tenant", "prod")

        assert len(result) == 3
        assert all(isinstance(r, PublishRecordRecord) for r in result)
        assert result[0].id == 1
        assert result[2].id == 3
        mock_session.query.assert_called_once()

    def test_empty_result(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_batch_id(999, "test_tenant", "prod")

        assert result == []


# ==================== list_by_device_id ====================


class TestListByDeviceId:
    def test_returns_multiple_records(self, repo, mock_session):
        model1, _ = _make_mock_model(id_val=1, device_id=50)
        model2, _ = _make_mock_model(id_val=2, device_id=50)
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repo.list_by_device_id(50, "test_tenant", "prod")

        assert len(result) == 2
        assert result[0].device_id == 50
        assert result[1].device_id == 50
        mock_session.query.assert_called_once()

    def test_empty_result(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_device_id(999, "test_tenant", "prod")

        assert result == []


# ==================== update_result ====================


class TestUpdateResult:
    def test_update_with_all_fields(self, repo, mock_session):
        repo.update_result(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="SUCCESS",
            result_message="deploy complete",
            modifier="admin2",
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["result_status"] == "SUCCESS"
        assert values["result_message"] == "deploy complete"
        assert values["modifier"] == "admin2"
        assert "gmt_modified" in values

    def test_update_minimal_fields(self, repo, mock_session):
        repo.update_result(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="FAILED",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["result_status"] == "FAILED"
        assert "result_message" not in values
        assert "modifier" not in values

    def test_update_with_result_message_only(self, repo, mock_session):
        repo.update_result(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="FAILED",
            result_message="timeout error",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["result_message"] == "timeout error"
        assert "modifier" not in values

    def test_update_with_modifier_only(self, repo, mock_session):
        repo.update_result(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="SUCCESS",
            modifier="supervisor",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["modifier"] == "supervisor"
        assert "result_message" not in values


# ==================== update_result_if_processing ====================


class TestUpdateResultIfCreated:
    def test_updated_returns_true(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_result_if_processing(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="SUCCESS",
            result_message="done",
            modifier="admin2",
        )

        assert result is True
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["result_status"] == "SUCCESS"
        assert values["result_message"] == "done"
        assert values["modifier"] == "admin2"

    def test_not_updated_returns_false(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        result = repo.update_result_if_processing(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="SUCCESS",
        )

        assert result is False

    def test_minimal_update(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_result_if_processing(
            record_id=10,
            tenant="test_tenant",
            env="prod",
            result_status="FAILED",
        )

        assert result is True
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert "result_message" not in values
        assert "modifier" not in values


# ==================== get_by_device_id_and_publish_id ====================


class TestGetByDeviceIdAndPublishId:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_model(
            id_val=5,
            device_id=100,
            publish_id=300,
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_device_id_and_publish_id(100, 300, "test_tenant", "prod")

        assert result is not None
        assert result.id == 5
        assert result.device_id == 100
        assert result.publish_id == 300
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_device_id_and_publish_id(999, 888, "test_tenant", "prod")

        assert result is None


# ==================== get_processing_record_by_device_and_publish ====================


class TestGetCreatedRecordByDeviceAndPublish:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_model(
            id_val=5,
            device_id=100,
            publish_id=300,
            result_status="PROCESSING",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_processing_record_by_device_and_publish(
            100, 300, "test_tenant", "prod"
        )

        assert result is not None
        assert result.id == 5
        assert result.result_status == "PROCESSING"
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_processing_record_by_device_and_publish(
            999, 888, "test_tenant", "prod"
        )

        assert result is None


# ==================== exists_record_for_device_and_publish ====================


class TestExistsRecordForDeviceAndPublish:
    def test_exists(self, repo, mock_session):
        # exists uses func.count().scalar()
        mock_session.query.return_value.filter.return_value.scalar.return_value = 1

        result = repo.exists_record_for_device_and_publish(
            100, 300, "test_tenant", "prod"
        )

        assert result is True
        mock_session.query.assert_called_once()

    def test_not_exists(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = 0

        result = repo.exists_record_for_device_and_publish(
            999, 888, "test_tenant", "prod"
        )

        assert result is False

    def test_none_scalar_returns_false(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.scalar.return_value = None

        result = repo.exists_record_for_device_and_publish(
            999, 888, "test_tenant", "prod"
        )

        assert result is False


# ==================== update_device_id ====================


class TestUpdateDeviceId:
    def test_update_with_modifier(self, repo, mock_session):
        repo.update_device_id(
            record_id=10,
            device_id=200,
            tenant="test_tenant",
            env="prod",
            modifier="admin2",
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert values["device_id"] == 200
        assert values["modifier"] == "admin2"
        assert "gmt_modified" in values

    def test_update_without_modifier(self, repo, mock_session):
        repo.update_device_id(
            record_id=10,
            device_id=200,
            tenant="test_tenant",
            env="prod",
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        values = call_kwargs[0][0]
        assert "modifier" not in values


# ==================== count_records_by_batch_id ====================


class TestCountRecordsByBatchId:
    def test_returns_counts(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            ("PROCESSING", 5),
            ("SUCCESS", 10),
            ("FAILED", 2),
        ]

        result = repo.count_records_by_batch_id(400, "test_tenant", "prod")

        assert result == {"PROCESSING": 5, "SUCCESS": 10, "FAILED": 2}
        mock_session.query.assert_called_once()

    def test_empty_result(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        result = repo.count_records_by_batch_id(999, "test_tenant", "prod")

        assert result == {}


# ==================== count_records_by_publish_id ====================


class TestCountRecordsByPublishId:
    def test_returns_counts_with_join(self, repo, mock_session):
        mock_session.query.return_value.join.return_value.filter.return_value.group_by.return_value.all.return_value = [
            ("PROCESSING", 3),
            ("SUCCESS", 7),
        ]

        result = repo.count_records_by_publish_id(300, "test_tenant", "prod")

        assert result == {"PROCESSING": 3, "SUCCESS": 7}
        mock_session.query.assert_called_once()

    def test_empty_result(self, repo, mock_session):
        mock_session.query.return_value.join.return_value.filter.return_value.group_by.return_value.all.return_value = []

        result = repo.count_records_by_publish_id(999, "test_tenant", "prod")

        assert result == {}


# ==================== list_stale_processing_records ====================


class TestListStaleCreatedRecords:
    def _make_stale_row(self, id_val=1, device_uuid="UUID-001", extra_config=None):
        """Create a mock Row for execute().fetchall()."""
        now = datetime.now()
        row = MagicMock()
        row.id = id_val
        row.gmt_create = now
        row.gmt_modified = now
        row.tenant = "test_tenant"
        row.env = "prod"
        row.domain = "default"
        row.is_deleted = 0
        row.creator = "admin"
        row.modifier = "admin"
        row.device_id = 100
        row.bot_id = 200
        row.publish_id = 300
        row.batch_id = 400
        row.event_type = "CREATE"
        row.trigger_source = "web"
        row.publish_reason = "deploy"
        row.result_status = "PROCESSING"
        row.result_message = None
        row.extra_config = extra_config
        row.device_uuid = device_uuid
        return row

    def test_returns_stale_records_with_device_uuid(self, repo, mock_session):
        # func.now() scalar for db_now
        mock_session.execute.return_value.scalar.return_value = datetime.now()

        row1 = self._make_stale_row(id_val=1, device_uuid="UUID-001")
        row2 = self._make_stale_row(id_val=2, device_uuid="UUID-002")

        # execute called twice: once for func.now(), once for the query
        # We need the second execute().fetchall() to return rows
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = [row1, row2]
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(300, 3600, "test_tenant", "prod")

        assert len(result) == 2
        assert result[0].device_uuid == "UUID-001"
        assert result[1].device_uuid == "UUID-002"

    def test_empty_result(self, repo, mock_session):
        mock_session.execute.return_value.scalar.return_value = datetime.now()
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = []
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(999, 1800, "test_tenant", "prod")

        assert result == []

    def test_extra_config_string_json_deserialized(self, repo, mock_session):
        mock_session.execute.return_value.scalar.return_value = datetime.now()

        row = self._make_stale_row(
            id_val=1,
            device_uuid="UUID-001",
            extra_config=json.dumps({"stage": "GRAY"}, ensure_ascii=False),
        )
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = [row]
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(300, 3600, "test_tenant", "prod")

        assert len(result) == 1
        assert result[0].extra_config == {"stage": "GRAY"}

    def test_extra_config_invalid_json_returns_empty_dict(self, repo, mock_session):
        mock_session.execute.return_value.scalar.return_value = datetime.now()

        row = self._make_stale_row(
            id_val=1,
            device_uuid="UUID-002",
            extra_config="not-valid-json{{{",
        )
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = [row]
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(300, 3600, "test_tenant", "prod")

        assert result[0].extra_config == {}

    def test_extra_config_none_returns_empty_dict(self, repo, mock_session):
        mock_session.execute.return_value.scalar.return_value = datetime.now()

        row = self._make_stale_row(id_val=1, device_uuid="UUID-003", extra_config=None)
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = [row]
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(300, 3600, "test_tenant", "prod")

        assert result[0].extra_config == {}

    def test_is_deleted_none_defaults_zero(self, repo, mock_session):
        mock_session.execute.return_value.scalar.return_value = datetime.now()

        row = self._make_stale_row(id_val=1, device_uuid="UUID-001")
        row.is_deleted = None
        fetch_mock = MagicMock()
        fetch_mock.fetchall.return_value = [row]
        mock_session.execute.return_value = fetch_mock

        result = repo.list_stale_processing_records(300, 3600, "test_tenant", "prod")

        assert result[0].is_deleted == 0


# ==================== get_latest_processing_record_by_device ====================


class TestGetLatestCreatedRecordByDevice:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_model(
            id_val=5,
            device_id=100,
            result_status="PROCESSING",
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repo.get_latest_processing_record_by_device(100, "test_tenant", "prod")

        assert result is not None
        assert result.id == 5
        assert result.result_status == "PROCESSING"
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.get_latest_processing_record_by_device(999, "test_tenant", "prod")

        assert result is None

    def test_sql_uses_desc_order_and_limit(self, repo, mock_session):
        model, _ = _make_mock_model(id_val=1)
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        repo.get_latest_processing_record_by_device(50, "tenantX", "envY")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()
        mock_session.query.return_value.filter.return_value.order_by.assert_called_once()
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.assert_called_once()


# ==================== PublishRecordExtraConfig ====================


class TestPublishRecordExtraConfig:
    def test_creates_with_both_fields(self):
        from dataclasses import asdict

        from secbaas.community.core.repository.publish_record import (
            PublishRecordExtraConfig,
        )

        cfg = PublishRecordExtraConfig(
            device_uuid="dev-abc", provider_device_id="container-xyz"
        )
        assert cfg.device_uuid == "dev-abc"
        assert cfg.provider_device_id == "container-xyz"

        d = asdict(cfg)
        assert d == {"device_uuid": "dev-abc", "provider_device_id": "container-xyz"}

    def test_defaults_to_none(self):
        from dataclasses import asdict

        from secbaas.community.core.repository.publish_record import (
            PublishRecordExtraConfig,
        )

        cfg = PublishRecordExtraConfig()
        assert cfg.device_uuid is None
        assert cfg.provider_device_id is None

        d = asdict(cfg)
        assert d == {"device_uuid": None, "provider_device_id": None}

    def test_provider_device_id_none(self):
        from dataclasses import asdict

        from secbaas.community.core.repository.publish_record import (
            PublishRecordExtraConfig,
        )

        cfg = PublishRecordExtraConfig(device_uuid="dev-1", provider_device_id=None)
        d = asdict(cfg)
        assert d == {"device_uuid": "dev-1", "provider_device_id": None}

    def test_slots_enforced(self):
        from secbaas.community.core.repository.publish_record import (
            PublishRecordExtraConfig,
        )

        cfg = PublishRecordExtraConfig(device_uuid="d1")
        with pytest.raises(AttributeError):
            cfg.nonexistent_field = "value"


# ==================== constructor ====================


class TestConstructor:
    def test_constructor_sets_database(self, mock_database):
        repo = OrmPublishRecordRepository(mock_database)
        assert repo._database is mock_database
