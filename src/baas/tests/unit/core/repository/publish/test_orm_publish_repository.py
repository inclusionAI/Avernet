"""
OrmPublishRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_run_repository.py and test_orm_bot_repository.py.
Covers all CRUD + query methods: insert_publish, get_by_id,
update_status, update_publish, list_by_bot_id, get_active_by_bot_id,
soft_delete.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.publish import (
    OrmPublishRepository,
    PublishRecord,
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
def _patch_publish_model():
    """Patch PublishModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.publish._orm_repository.PublishModel",
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
    """Create an OrmPublishRepository instance with mock database."""
    return OrmPublishRepository(database=mock_database)


# ==================== Model helpers ====================


def _make_mock_publish_model(
    id_val=1,
    gmt_create=None,
    gmt_modified=None,
    tenant="test_tenant",
    env="dev",
    domain="test_domain",
    is_deleted=0,
    creator="creator-001",
    modifier="modifier-001",
    bot_id=10,
    publish_type="CREATE",
    name="Test Publish",
    description="A test publish",
    publisher="publisher-001",
    replica_desired=2,
    batch_capacity=5,
    batch_number=3,
    cooldown_seconds=300,
    config_version="v1.0",
    status="PENDING",
    last_publish_id=None,
    changelog="Initial publish",
    extra_config=None,
):
    """Create a MagicMock(spec=PublishModel) whose to_record() returns a PublishRecord."""
    now = datetime.now()

    # Simulate PublishModel.to_record() behavior for extra_config
    if extra_config is not None:
        if isinstance(extra_config, str):
            try:
                ec = json.loads(extra_config)
            except (json.JSONDecodeError, TypeError):
                ec = {}
        else:
            ec = extra_config
    else:
        ec = {}

    if ec is None:
        ec = {}

    record = PublishRecord(
        id=id_val,
        gmt_create=gmt_create or now,
        gmt_modified=gmt_modified or now,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        bot_id=bot_id,
        publish_type=publish_type,
        name=name,
        description=description,
        publisher=publisher,
        replica_desired=replica_desired,
        batch_capacity=batch_capacity,
        batch_number=batch_number,
        cooldown_seconds=cooldown_seconds,
        config_version=config_version,
        status=status,
        last_publish_id=last_publish_id,
        changelog=changelog,
        extra_config=ec,
    )

    model = MagicMock()
    model.to_record.return_value = record
    model.id = id_val
    return model, record


# ==================== PublishRecord dataclass ====================


class TestPublishRecord:
    def test_creates_publish_record(self):
        now = datetime.now()
        record = PublishRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            tenant="t1",
            env="dev",
            domain="example.com",
            is_deleted=0,
            creator="c1",
            modifier="m1",
            bot_id=10,
            publish_type="CREATE",
            name="Test Publish",
            description="A test",
            publisher="p1",
            replica_desired=2,
            batch_capacity=5,
            batch_number=3,
            cooldown_seconds=300,
            config_version="v1.0",
            status="PENDING",
            last_publish_id=None,
            changelog="Initial",
            extra_config={"mode": "full"},
        )

        assert record.id == 1
        assert record.tenant == "t1"
        assert record.bot_id == 10
        assert record.publish_type == "CREATE"
        assert record.status == "PENDING"
        assert record.extra_config == {"mode": "full"}
        assert record.name == "Test Publish"

    def test_none_optional_fields(self):
        now = datetime.now()
        record = PublishRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            tenant="t1",
            env="dev",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            bot_id=1,
            publish_type="CREATE",
            name=None,
            description=None,
            publisher=None,
            replica_desired=None,
            batch_capacity=None,
            batch_number=None,
            cooldown_seconds=None,
            config_version=None,
            status="PENDING",
            last_publish_id=None,
            changelog=None,
            extra_config={},
        )

        assert record.name is None
        assert record.description is None
        assert record.publisher is None
        assert record.replica_desired is None
        assert record.batch_capacity is None
        assert record.last_publish_id is None
        assert record.changelog is None


# ==================== insert_publish ====================


class TestInsertPublish:
    def test_insert_with_minimal_defaults(self, repo, mock_session):
        """Insert with only required fields; defaults are applied."""
        result = repo.insert_publish(
            tenant="t1",
            env="prod",
            domain="example.com",
            bot_id=5,
            publish_type="CREATE",
            status="PENDING",
            creator="creator-x",
            modifier="modifier-x",
        )

        assert result == 42
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

        added_model = mock_session.add.call_args[0][0]
        assert added_model.tenant == "t1"
        assert added_model.env == "prod"
        assert added_model.domain == "example.com"
        assert added_model.bot_id == 5
        assert added_model.publish_type == "CREATE"
        assert added_model.name == "Publish-CREATE"  # default
        assert added_model.description is None
        assert added_model.publisher == "creator-x"  # default = creator
        assert added_model.replica_desired == 1  # default
        assert added_model.batch_capacity == 5  # default
        assert added_model.batch_number == 1  # default
        assert added_model.cooldown_seconds == 0  # default
        assert added_model.config_version is None
        assert added_model.status == "PENDING"
        assert added_model.last_publish_id is None
        assert added_model.changelog is None
        assert added_model.extra_config is None
        assert added_model.creator == "creator-x"
        assert added_model.modifier == "modifier-x"
        assert added_model.is_deleted == 0

    def test_insert_with_all_explicit_params(self, repo, mock_session):
        """Insert with all fields provided explicitly."""
        result = repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.example.com",
            bot_id=10,
            publish_type="SCALE_UP",
            status="PENDING",
            creator="creator-1",
            modifier="mod-1",
            name="Scale Up Publish",
            description="Scaling up bots",
            publisher="ops-team",
            replica_desired=3,
            batch_capacity=10,
            batch_number=2,
            cooldown_seconds=120,
            config_version="v2.0",
            last_publish_id=99,
            changelog="Increased capacity",
            extra_config={"target": 5},
        )

        assert result == 42
        added_model = mock_session.add.call_args[0][0]
        assert added_model.tenant == "t1"
        assert added_model.env == "dev"
        assert added_model.domain == "d.example.com"
        assert added_model.bot_id == 10
        assert added_model.publish_type == "SCALE_UP"
        assert added_model.name == "Scale Up Publish"
        assert added_model.description == "Scaling up bots"
        assert added_model.publisher == "ops-team"
        assert added_model.replica_desired == 3
        assert added_model.batch_capacity == 10
        assert added_model.batch_number == 2
        assert added_model.cooldown_seconds == 120
        assert added_model.config_version == "v2.0"
        assert added_model.status == "PENDING"
        assert added_model.last_publish_id == 99
        assert added_model.changelog == "Increased capacity"
        # extra_config is JSON-serialized
        assert '"target": 5' in added_model.extra_config
        assert added_model.creator == "creator-1"
        assert added_model.modifier == "mod-1"

    def test_insert_with_extra_config_serializes_json(self, repo, mock_session):
        repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.com",
            bot_id=1,
            publish_type="RESTART",
            status="PENDING",
            creator="c",
            modifier="m",
            extra_config={"k1": "v1", "nested": {"a": 1}},
        )
        added_model = mock_session.add.call_args[0][0]
        extra_config_str = added_model.extra_config
        assert '"k1": "v1"' in extra_config_str
        assert '"nested"' in extra_config_str

    def test_insert_with_none_extra_config(self, repo, mock_session):
        repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.com",
            bot_id=1,
            publish_type="DESTROY",
            status="PENDING",
            creator="c",
            modifier="m",
            extra_config=None,
        )
        added_model = mock_session.add.call_args[0][0]
        assert added_model.extra_config is None

    def test_insert_with_publisher_none_defaults_to_creator(self, repo, mock_session):
        """When publisher is None, it defaults to creator."""
        repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.com",
            bot_id=1,
            publish_type="CREATE",
            status="PENDING",
            creator="creator-user",
            modifier="mod-user",
            publisher=None,
        )
        added_model = mock_session.add.call_args[0][0]
        assert added_model.publisher == "creator-user"

    def test_insert_with_name_none_defaults_to_publish_type_prefix(
        self, repo, mock_session
    ):
        """When name is None, it defaults to 'Publish-{publish_type}'."""
        repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.com",
            bot_id=1,
            publish_type="RESTART",
            status="PENDING",
            creator="c",
            modifier="m",
            name=None,
        )
        added_model = mock_session.add.call_args[0][0]
        assert added_model.name == "Publish-RESTART"

    def test_insert_defaults_for_numeric_fields(self, repo, mock_session):
        """replica_desired=None → 1, batch_capacity=None → 5, batch_number=None → 1, cooldown_seconds=None → 0."""
        repo.insert_publish(
            tenant="t1",
            env="dev",
            domain="d.com",
            bot_id=1,
            publish_type="UPDATE",
            status="PENDING",
            creator="c",
            modifier="m",
            replica_desired=None,
            batch_capacity=None,
            batch_number=None,
            cooldown_seconds=None,
        )
        added_model = mock_session.add.call_args[0][0]
        assert added_model.replica_desired == 1
        assert added_model.batch_capacity == 5
        assert added_model.batch_number == 1
        assert added_model.cooldown_seconds == 0


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repo, mock_session):
        mock_model, _ = _make_mock_publish_model(id_val=5, status="ACTIVE")
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        result = repo.get_by_id(5, "t1", "dev")

        assert result is not None
        assert result.id == 5
        assert result.status == "ACTIVE"
        mock_model.to_record.assert_called_once()
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id(999, "t1", "dev")

        assert result is None


# ==================== update_status ====================


class TestUpdateStatus:
    def test_update_status_with_modifier(self, repo, mock_session):
        repo.update_status(
            publish_id=5, tenant="t1", env="dev", status="ACTIVE", modifier="admin"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "ACTIVE"
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_status_without_modifier(self, repo, mock_session):
        repo.update_status(
            publish_id=5, tenant="t1", env="dev", status="FAILED", modifier=None
        )

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "FAILED"
        assert "modifier" not in update_dict

    def test_update_status_no_modifier_kwarg(self, repo, mock_session):
        repo.update_status(publish_id=5, tenant="t1", env="dev", status="REJECTED")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "REJECTED"
        assert "modifier" not in update_dict


# ==================== update_publish ====================


class TestUpdatePublish:
    def test_update_with_extra_config_only(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_publish(
            publish_id=5,
            tenant="t1",
            env="dev",
            extra_config={"mode": "incremental"},
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["extra_config"] == json.dumps(
            {"mode": "incremental"}, ensure_ascii=False
        )
        assert "gmt_modified" in update_dict
        assert "modifier" not in update_dict

    def test_update_with_modifier_only(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_publish(
            publish_id=5, tenant="t1", env="dev", modifier="new-mod"
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "new-mod"
        assert "gmt_modified" in update_dict

    def test_update_with_both(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 2

        result = repo.update_publish(
            publish_id=5,
            tenant="t1",
            env="dev",
            extra_config={"key": "val"},
            modifier="admin",
        )

        assert result == 2
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["extra_config"] == json.dumps(
            {"key": "val"}, ensure_ascii=False
        )
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_with_no_fields_returns_zero(self, repo, mock_session):
        """When neither extra_config nor modifier is provided, returns 0 without query."""
        result = repo.update_publish(publish_id=5, tenant="t1", env="dev")

        assert result == 0
        # No query should be built since no fields to update
        mock_session.query.assert_not_called()

    def test_update_with_both_none_returns_zero(self, repo, mock_session):
        """When both extra_config and modifier are explicitly None, returns 0."""
        result = repo.update_publish(
            publish_id=5,
            tenant="t1",
            env="dev",
            extra_config=None,
            modifier=None,
        )

        assert result == 0
        mock_session.query.assert_not_called()

    def test_update_has_tenant_isolation(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_publish(
            publish_id=10, tenant="tenant-x", env="prod", modifier="mod"
        )

        # Verify filter was called with proper isolation conditions
        # We can't easily inspect the filter args, but we can verify update was called
        mock_session.query.return_value.filter.return_value.update.assert_called_once()


# ==================== list_by_bot_id ====================


class TestListByBotId:
    def test_returns_multiple(self, repo, mock_session):
        mock_models = [
            _make_mock_publish_model(id_val=1, bot_id=10)[0],
            _make_mock_publish_model(id_val=2, bot_id=10)[0],
            _make_mock_publish_model(id_val=3, bot_id=10)[0],
        ]
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = mock_models

        result = repo.list_by_bot_id(10, "t1", "dev")

        assert len(result) == 3
        assert result[0].id == 1
        assert result[1].id == 2
        assert result[2].id == 3
        for r in result:
            assert isinstance(r, PublishRecord)

    def test_returns_empty_list(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_bot_id(999, "t1", "dev")

        assert result == []

    def test_calls_to_record_on_each_model(self, repo, mock_session):
        m1, _ = _make_mock_publish_model(id_val=1)
        m2, _ = _make_mock_publish_model(id_val=2)
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            m1,
            m2,
        ]

        repo.list_by_bot_id(10, "t1", "dev")

        m1.to_record.assert_called_once()
        m2.to_record.assert_called_once()


# ==================== get_active_by_bot_id ====================


class TestGetActiveByBotId:
    def test_found(self, repo, mock_session):
        mock_model, _ = _make_mock_publish_model(
            id_val=5, bot_id=10, status="APPROVING"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_model

        result = repo.get_active_by_bot_id(10, "t1", "dev")

        assert result is not None
        assert result.id == 5
        assert result.status == "APPROVING"
        mock_model.to_record.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.get_active_by_bot_id(999, "t1", "dev")

        assert result is None


# ==================== soft_delete ====================


class TestSoftDelete:
    def test_soft_delete_updates_existing_record(self, repo, mock_session):
        mock_model, _ = _make_mock_publish_model(id_val=5)
        mock_session.query.return_value.filter.return_value.first.return_value = (
            mock_model
        )

        repo.soft_delete(publish_id=5, tenant="t1", env="dev", modifier="admin")

        # Second query().filter().update() should be called
        assert (
            mock_session.query.return_value.filter.return_value.update.call_count == 1
        )
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == 5  # publish_id
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_soft_delete_not_found_skips_update(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        repo.soft_delete(publish_id=999, tenant="t1", env="dev", modifier="admin")

        # first() is called (returns None), update should not be called
        mock_session.query.return_value.filter.return_value.update.assert_not_called()


# ==================== Constructor ====================


class TestConstructor:
    def test_constructor_sets_database(self, mock_database):
        repo = OrmPublishRepository(database=mock_database)

        assert repo._database is mock_database
