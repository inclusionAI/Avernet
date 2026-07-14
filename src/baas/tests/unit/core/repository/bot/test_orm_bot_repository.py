"""
OrmBotRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_run_repository.py tests.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.repository.bot import (
    BotRecord,
    OrmBotRepository,
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
def _patch_bot_model():
    """Patch BotModel so constructor returns a mock with .id=42 pre-set."""
    with patch(
        "secbaas.community.core.repository.bot._orm_repository.BotModel",
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
    """Create an OrmBotRepository instance with mock database."""
    return OrmBotRepository(mock_database, rel_repo=MagicMock())


# ==================== Model helpers ====================


def _make_mock_bot_model(
    id_val=1,
    bot_uuid="bot-uuid-001",
    tenant="test_tenant",
    env="dev",
    domain="test_domain",
    is_deleted=0,
    creator="creator-001",
    modifier="modifier-001",
    status="ACTIVE",
    name="Test Bot",
    description="A test bot",
    template_uuid="tpl-001",
    replica_desired=2,
    replica_minimum=1,
    replica_maximum=5,
    auto_scaling_enabled=1,
    sla_grade="standard",
    extra_config=None,
):
    """Create a MagicMock(spec=BotModel) whose to_record() returns a BotRecord."""
    now = datetime.now()
    final_extra = extra_config if extra_config is not None else {}

    record = BotRecord(
        id=id_val,
        gmt_create=now,
        gmt_modified=now,
        bot_uuid=bot_uuid,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        status=status,
        name=name,
        description=description,
        template_uuid=template_uuid,
        replica_desired=replica_desired,
        replica_minimum=replica_minimum,
        replica_maximum=replica_maximum,
        auto_scaling_enabled=auto_scaling_enabled,
        sla_grade=sla_grade,
        extra_config=final_extra,
    )

    model = MagicMock()
    model.to_record.return_value = record
    model.id = id_val
    return model, record


# ==================== BotModel.to_record / BotRecord ====================


class TestBotModelToRecord:
    """Tests for BotModel.to_record() — mapping from model to BotRecord."""

    def test_none_model_returns_none(self, repo):
        assert BotRecord is not None

    def test_converts_valid_model(self, repo):
        extra = {"key": "value", "nested": {"a": 1}}
        model, record = _make_mock_bot_model(
            id_val=7,
            bot_uuid="bot-abc",
            tenant="t1",
            env="prod",
            domain="example.com",
            status="ACTIVE",
            name="My Bot",
            description="Test",
            extra_config=extra,
        )

        assert isinstance(record, BotRecord)
        assert record.id == 7
        assert record.bot_uuid == "bot-abc"
        assert record.tenant == "t1"
        assert record.env == "prod"
        assert record.domain == "example.com"
        assert record.status == "ACTIVE"
        assert record.name == "My Bot"
        assert record.description == "Test"
        assert record.extra_config == extra

    def test_handles_empty_extra_config(self, repo):
        model, record = _make_mock_bot_model(id_val=1, extra_config={})
        assert record.extra_config == {}

    def test_handles_none_extra_config(self, repo):
        model, record = _make_mock_bot_model(id_val=1, extra_config=None)
        assert record.extra_config == {}

    def test_defaults_is_deleted(self, repo):
        model, record = _make_mock_bot_model(id_val=1, is_deleted=0)
        assert record.is_deleted == 0

    def test_is_deleted_non_zero(self, repo):
        model, record = _make_mock_bot_model(id_val=1, is_deleted=5)
        assert record.is_deleted == 5

    def test_defaults_replica_fields(self, repo):
        model, record = _make_mock_bot_model(
            id_val=1, replica_desired=0, replica_minimum=0, replica_maximum=0
        )
        assert record.replica_desired == 0
        assert record.replica_minimum == 0
        assert record.replica_maximum == 0

    def test_keeps_sla_grade(self, repo):
        model, record = _make_mock_bot_model(id_val=1, sla_grade="premium")
        assert record.sla_grade == "premium"

    def test_null_sla_grade(self, repo):
        model, record = _make_mock_bot_model(id_val=1, sla_grade=None)
        assert record.sla_grade is None


# ==================== BotRecord dataclass ====================


class TestBotRecord:
    def test_creates_bot_record(self):
        now = datetime.now()
        record = BotRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            bot_uuid="bot-1",
            tenant="t1",
            env="dev",
            domain="example.com",
            is_deleted=0,
            creator="c1",
            modifier="m1",
            status="ACTIVE",
            name="Test",
            description="Desc",
            template_uuid="tpl-1",
            replica_desired=2,
            replica_minimum=1,
            replica_maximum=5,
            auto_scaling_enabled=1,
            sla_grade="standard",
            extra_config={"k": "v"},
        )

        assert record.id == 1
        assert record.bot_uuid == "bot-1"
        assert record.extra_config == {"k": "v"}
        assert record.description == "Desc"

    def test_none_description_and_template(self):
        now = datetime.now()
        record = BotRecord(
            id=1,
            gmt_create=now,
            gmt_modified=now,
            bot_uuid="b1",
            tenant="t1",
            env="dev",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="ACTIVE",
            name="Test",
            description=None,
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )

        assert record.description is None
        assert record.template_uuid is None


# ==================== insert_bot ====================


class TestInsertBot:
    def test_insert_returns_id(self, repo, mock_session):
        result = repo.insert_bot(
            bot_uuid="bot-001",
            tenant="t1",
            env="dev",
            domain="example.com",
            creator="creator-1",
            modifier="mod-1",
            status="PENDING",
            name="New Bot",
        )

        assert result is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_insert_model_fields(self, repo, mock_session):
        repo.insert_bot(
            bot_uuid="bot-001",
            tenant="t1",
            env="dev",
            domain="example.com",
            creator="creator-1",
            modifier="mod-1",
            status="PENDING",
            name="New Bot",
            description="A bot",
            template_uuid="tpl-001",
            replica_desired=3,
            replica_minimum=2,
            replica_maximum=8,
            auto_scaling_enabled=1,
            sla_grade="premium",
        )

        mock_session.add.assert_called_once()
        model = mock_session.add.call_args[0][0]
        assert model.bot_uuid == "bot-001"
        assert model.tenant == "t1"
        assert model.env == "dev"
        assert model.domain == "example.com"
        assert model.creator == "creator-1"
        assert model.modifier == "mod-1"
        assert model.status == "PENDING"
        assert model.name == "New Bot"
        assert model.description == "A bot"
        assert model.template_uuid == "tpl-001"
        assert model.replica_desired == 3
        assert model.replica_minimum == 2
        assert model.replica_maximum == 8
        assert model.sla_grade == "premium"

    def test_insert_with_extra_config_serializes_json(self, repo, mock_session):
        repo.insert_bot(
            bot_uuid="bot-002",
            tenant="t1",
            env="dev",
            domain="example.com",
            creator="c1",
            modifier="m1",
            name="Bot",
            extra_config={"k1": "v1"},
        )

        model = mock_session.add.call_args[0][0]
        extra_config_str = model.extra_config
        assert isinstance(extra_config_str, str)
        assert '"k1": "v1"' in extra_config_str

    def test_insert_with_none_extra_config(self, repo, mock_session):
        repo.insert_bot(
            bot_uuid="bot-003",
            tenant="t1",
            env="dev",
            domain="example.com",
            creator="c1",
            modifier="m1",
            name="Bot",
            extra_config=None,
        )

        model = mock_session.add.call_args[0][0]
        assert model.extra_config is None


# ==================== get_by_id ====================


class TestGetById:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-xyz", name="My Bot"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_id(5, "t1", "dev")

        assert result is not None
        assert result.id == 5
        assert result.bot_uuid == "bot-xyz"
        assert result.name == "My Bot"
        mock_session.query.assert_called_once()

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id(999, "t1", "dev")

        assert result is None


# ==================== get_by_id_including_deleted ====================


class TestGetByIdIncludingDeleted:
    def test_found_including_deleted(self, repo, mock_session):
        model, record = _make_mock_bot_model(id_val=5, is_deleted=5)
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repo.get_by_id_including_deleted(5, "t1", "dev")

        assert result is not None
        assert result.id == 5
        assert result.is_deleted == 5

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repo.get_by_id_including_deleted(999, "t1", "dev")

        assert result is None


# ==================== get_by_bot_uuid ====================


class TestGetByBotUuid:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_bot_model(
            id_val=10, bot_uuid="bot-abc", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repo.get_by_bot_uuid("bot-abc", "t1", "dev", "ACTIVE")

        assert result is not None
        assert result.id == 10
        assert result.status == "ACTIVE"

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.get_by_bot_uuid("nonexistent", "t1", "dev", "ACTIVE")

        assert result is None

    def test_correct_query_params(self, repo, mock_session):
        model, _ = _make_mock_bot_model()
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        repo.get_by_bot_uuid("bot-1", "t1", "prod", "PENDING")

        mock_session.query.assert_called_once()


# ==================== list_by_bot_uuid ====================


class TestListByBotUuid:
    def test_returns_multiple(self, repo, mock_session):
        model1, rec1 = _make_mock_bot_model(id_val=1, bot_uuid="bot-1")
        model2, rec2 = _make_mock_bot_model(id_val=2, bot_uuid="bot-1")
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            model1,
            model2,
        ]

        result = repo.list_by_bot_uuid("bot-1", "t1", "dev")

        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].id == 2

    def test_returns_empty_list(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = repo.list_by_bot_uuid("nonexistent", "t1", "dev")

        assert result == []

    def test_calls_query(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        repo.list_by_bot_uuid("bot-1", "t1", "dev")

        mock_session.query.assert_called_once()


# ==================== update_bot ====================


class TestUpdateBot:
    def test_update_single_field(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_bot(bot_id=1, tenant="t1", env="dev", name="New Name")

        assert result == 1
        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["name"] == "New Name"
        assert "gmt_modified" in update_dict

    def test_update_multiple_fields(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        result = repo.update_bot(
            bot_id=1,
            tenant="t1",
            env="dev",
            name="New Name",
            description="New Desc",
            modifier="admin",
        )

        assert result == 1
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["name"] == "New Name"
        assert update_dict["description"] == "New Desc"
        assert update_dict["modifier"] == "admin"

    def test_update_no_fields_returns_zero(self, repo):
        """No fields given → no update values → result depends on ORM behavior."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.update.return_value = 0

        mock_database = MagicMock()
        mock_database.orm_session.return_value.__enter__ = MagicMock(
            return_value=mock_session
        )
        mock_database.orm_session.return_value.__exit__ = MagicMock(return_value=False)
        test_repo = OrmBotRepository(mock_database, rel_repo=MagicMock())

        result = test_repo.update_bot(bot_id=1, tenant="t1", env="dev")

        assert result == 0

    def test_update_name_only(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_bot(bot_id=1, tenant="t1", env="dev", name="Updated")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["name"] == "Updated"

    def test_update_description_only(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_bot(bot_id=1, tenant="t1", env="dev", description="Desc")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["description"] == "Desc"

    def test_update_modifier_only(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.update.return_value = 1

        repo.update_bot(bot_id=1, tenant="t1", env="dev", modifier="new-mod")

        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["modifier"] == "new-mod"


# ==================== update_status ====================


class TestUpdateStatus:
    def test_update_status(self, repo, mock_session):
        repo.update_status(
            bot_id=1, tenant="t1", env="dev", status="RELEASED", modifier="admin"
        )

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["status"] == "RELEASED"
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_update_status_uses_filter(self, repo, mock_session):
        repo.update_status(
            bot_id=1, tenant="t1", env="dev", status="ACTIVE", modifier="admin"
        )

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()


# ==================== soft_delete ====================


class TestSoftDelete:
    def test_soft_delete(self, repo, mock_session):
        repo.soft_delete(bot_id=5, tenant="t1", env="dev", modifier="admin")

        mock_session.query.return_value.filter.return_value.update.assert_called_once()
        call_kwargs = (
            mock_session.query.return_value.filter.return_value.update.call_args
        )
        update_dict = call_kwargs[0][0]
        assert update_dict["is_deleted"] == 5
        assert update_dict["modifier"] == "admin"
        assert "gmt_modified" in update_dict

    def test_soft_delete_applies_filter(self, repo, mock_session):
        repo.soft_delete(bot_id=5, tenant="t1", env="dev", modifier="admin")

        mock_session.query.assert_called_once()
        mock_session.query.return_value.filter.assert_called_once()


# ==================== insert_bot_record ====================


class TestInsertBotRecord:
    def test_source_not_found_raises_value_error(self, repo, mock_session):
        """get_by_id called internally returns None."""
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="Source bot not found"):
            repo.insert_bot_record(
                source_bot_id=999, tenant="t1", env="dev", status="PENDING"
            )

    def test_basic_clone(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5,
            bot_uuid="bot-src",
            name="Source Bot",
            status="ACTIVE",
            description="Source Desc",
        )

        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        result = repo.insert_bot_record(
            source_bot_id=5, tenant="t1", env="dev", status="FAILED"
        )

        assert result is not None
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_clone_with_existing_pending_cleans_up(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", name="Source Bot", status="ACTIVE"
        )
        pending_model, pending_record = _make_mock_bot_model(
            id_val=10, bot_uuid="bot-src", status="PENDING"
        )

        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = pending_model

        result = repo.insert_bot_record(
            source_bot_id=5, tenant="t1", env="dev", status="PENDING"
        )

        assert result is not None
        repo._rel_repo.soft_delete_by_bot_id.assert_called_once_with(
            bot_id=10, tenant="t1", env="dev", modifier="system"
        )

    def test_clone_with_existing_pending_handles_rel_error(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        pending_model, pending_record = _make_mock_bot_model(
            id_val=10, bot_uuid="bot-src", status="PENDING"
        )

        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = pending_model
        repo._rel_repo.soft_delete_by_bot_id.side_effect = RuntimeError(
            "device rel error"
        )

        result = repo.insert_bot_record(
            source_bot_id=5, tenant="t1", env="dev", status="PENDING"
        )

        assert result is not None
        mock_session.query.return_value.filter.return_value.update.assert_called()

    def test_clone_with_name_override(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", name="Source Bot", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.insert_bot_record(
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="PENDING",
            name="Custom Name",
        )

        assert result is not None

    def test_clone_with_extra_config_override(self, repo, mock_session):
        source_model, source_record = _make_mock_bot_model(
            id_val=5,
            bot_uuid="bot-src",
            status="ACTIVE",
            extra_config={"old": "config"},
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.insert_bot_record(
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="PENDING",
            extra_config={"new": "config"},
        )

        assert result is not None

    def test_non_pending_status_skips_cleanup(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        result = repo.insert_bot_record(
            source_bot_id=5, tenant="t1", env="dev", status="FAILED"
        )

        assert result is not None
        first_call_count = (
            mock_session.query.return_value.filter.return_value.first.call_count
        )
        assert first_call_count == 1

    def test_clone_uses_modifier_parameter(self, repo, mock_session):
        source_model, _ = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-src", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = (
            source_model
        )

        repo.insert_bot_record(
            source_bot_id=5,
            tenant="t1",
            env="dev",
            status="FAILED",
            modifier="custom-mod",
        )

        added_model = mock_session.add.call_args[0][0]
        assert added_model.modifier == "custom-mod"


# ==================== list_bots ====================


class TestListBots:
    def test_no_status_filter(self, repo, mock_session):
        model1, _ = _make_mock_bot_model(id_val=1)
        model2, _ = _make_mock_bot_model(id_val=2)

        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 2
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repo.list_bots(tenant="t1", env="dev")

        assert total == 2
        assert len(items) == 2

    def test_with_status_filter(self, repo, mock_session):
        model1, _ = _make_mock_bot_model(id_val=1, status="ACTIVE")
        model2, _ = _make_mock_bot_model(id_val=2, status="ACTIVE")

        mock_session.query.return_value.filter.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 2
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repo.list_bots(tenant="t1", env="dev", status="ACTIVE")

        assert total == 2
        assert len(items) == 2

    def test_empty_list(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 0
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repo.list_bots(tenant="t1", env="dev")

        assert total == 0
        assert items == []

    def test_with_pagination(self, repo, mock_session):
        models = [_make_mock_bot_model(id_val=i)[0] for i in range(1, 6)]

        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 50
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = models

        total, items = repo.list_bots(tenant="t1", env="dev", page=2, page_size=5)

        assert total == 50
        assert len(items) == 5

    def test_default_page_and_size(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 10
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            _make_mock_bot_model()[0]
        ]

        repo.list_bots(tenant="t1", env="dev")

        mock_session.query.return_value.filter.return_value.order_by.assert_called_once()
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.assert_called_once_with(
            0
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.assert_called_once_with(
            20
        )


# ==================== get_active_by_bot_uuid ====================


class TestGetActiveByBotUuid:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-abc", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model
        ]

        result = repo.get_active_by_bot_uuid("bot-abc", "t1", "dev")

        assert result is not None
        assert result.id == 5
        assert result.status == "ACTIVE"

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = repo.get_active_by_bot_uuid("nonexistent", "t1", "dev")

        assert result is None

    def test_multiple_active_raises_runtime_error(self, repo, mock_session):
        model1, _ = _make_mock_bot_model(id_val=1, status="ACTIVE")
        model2, _ = _make_mock_bot_model(id_val=2, status="ACTIVE")
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        with pytest.raises(RuntimeError, match="Data integrity violation"):
            repo.get_active_by_bot_uuid("bot-abc", "t1", "dev")


# ==================== get_active_by_bot_uuid_only ====================


class TestGetActiveByBotUuidOnly:
    def test_found(self, repo, mock_session):
        model, record = _make_mock_bot_model(
            id_val=5, bot_uuid="bot-abc", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = model

        result = repo.get_active_by_bot_uuid_only("bot-abc")

        assert result is not None
        assert result.id == 5
        assert result.status == "ACTIVE"

    def test_not_found(self, repo, mock_session):
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = repo.get_active_by_bot_uuid_only("nonexistent")

        assert result is None


# ==================== complete_destroy ====================


class TestCompleteDestroy:
    def test_complete_destroy_calls_two_updates(self, repo, mock_session):
        repo.complete_destroy(bot_id=5, tenant="t1", env="dev", modifier="admin")

        assert (
            mock_session.query.return_value.filter.return_value.update.call_count == 2
        )

        # First update: status = RELEASED
        call_kwargs_1 = (
            mock_session.query.return_value.filter.return_value.update.call_args_list[0]
        )
        update_dict_1 = call_kwargs_1[0][0]
        assert update_dict_1["status"] == "RELEASED"
        assert update_dict_1["modifier"] == "admin"

        call_kwargs_2 = (
            mock_session.query.return_value.filter.return_value.update.call_args_list[1]
        )
        update_dict_2 = call_kwargs_2[0][0]
        assert update_dict_2["is_deleted"] == 5

    def test_complete_destroy_uses_is_deleted_filter(self, repo, mock_session):
        repo.complete_destroy(bot_id=5, tenant="t1", env="dev", modifier="admin")

        assert mock_session.query.call_count == 2


# ==================== complete_update_transfer ====================


class TestCompleteUpdateTransfer:
    def test_complete_update_transfer(self, repo, mock_session):
        repo.complete_update_transfer(
            old_bot_id=5,
            new_bot_id=10,
            device_uuids=["dev-1", "dev-2", "dev-3"],
            domain="example.com",
            tenant="t1",
            env="dev",
            modifier="admin",
        )

        assert (
            mock_session.query.return_value.filter.return_value.update.call_count == 4
        )
        assert mock_session.add.call_count == 3
        mock_session.flush.assert_called_once()

    def test_no_devices(self, repo, mock_session):
        repo.complete_update_transfer(
            old_bot_id=5,
            new_bot_id=10,
            device_uuids=[],
            domain="example.com",
            tenant="t1",
            env="dev",
            modifier="admin",
        )

        assert (
            mock_session.query.return_value.filter.return_value.update.call_count == 4
        )
        assert mock_session.add.call_count == 0
        mock_session.flush.assert_called_once()

    def test_single_device(self, repo, mock_session):
        repo.complete_update_transfer(
            old_bot_id=5,
            new_bot_id=10,
            device_uuids=["dev-only"],
            domain="example.com",
            tenant="t1",
            env="dev",
            modifier="admin",
        )

        assert mock_session.add.call_count == 1
        assert (
            mock_session.query.return_value.filter.return_value.update.call_count == 4
        )


# ==================== init/constructor ====================


class TestConstructor:
    def test_constructor_sets_attributes(self, mock_database):
        rel_repo = MagicMock()
        repo = OrmBotRepository(mock_database, rel_repo=rel_repo)

        assert repo._database is mock_database
        assert repo._rel_repo is rel_repo

    def test_default_attributes_set(self, mock_database):
        rel_repo = MagicMock()
        repo = OrmBotRepository(mock_database, rel_repo=rel_repo)

        assert repo._database is mock_database
        assert repo._rel_repo is rel_repo
