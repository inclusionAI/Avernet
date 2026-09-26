"""
OrmAcBotRepository unit tests.

Uses pytest + MagicMock ORM session pattern matching existing
test_orm_bot_repository.py tests.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.ac_bot import AcBotRecord, OrmAcBotRepository

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


@pytest.fixture
def repository(mock_database):
    """Create an OrmAcBotRepository instance with mock database."""
    return OrmAcBotRepository(mock_database)


# ==================== Model helpers ====================


def _make_mock_model(
    id_val=1,
    bot_id="bot-001",
    bot_name="Test Bot",
    bot_desc="A test bot",
    entity_id="entity-001",
    entity_type="staff",
    creator_id="creator-001",
    owner_id="owner-001",
    engine_types="[]",
    status="online",
    binding_id=100,
    gmt_create=None,
    gmt_modified=None,
    modifier_id="mod-001",
    share_policy="{}",
    is_delete=0,
    active_engine="openclaw",
    device_id="device-001",
    env="prod",
    owner_name="Owner",
    public="0",
    ext="{}",
    bot_type="personal",
):
    """Create a MagicMock model with AcBotModel-compatible attributes."""
    now = datetime.now()
    model = MagicMock()
    model.id = id_val
    model.bot_id = bot_id
    model.bot_name = bot_name
    model.bot_desc = bot_desc
    model.entity_id = entity_id
    model.entity_type = entity_type
    model.creator_id = creator_id
    model.owner_id = owner_id
    model.engine_types = engine_types
    model.status = status
    model.binding_id = binding_id
    model.gmt_create = gmt_create or now
    model.gmt_modified = gmt_modified or now
    model.modifier_id = modifier_id
    model.share_policy = share_policy
    model.is_delete = is_delete
    model.active_engine = active_engine
    model.device_id = device_id
    model.env = env
    model.owner_name = owner_name
    model.public = public
    model.ext = ext
    model.bot_type = bot_type
    return model


def _make_mock_model_with_json(
    id_val=1,
    bot_id="bot-001",
    engine_types=None,
    share_policy=None,
    ext=None,
    **kwargs,
):
    """Create model with actual json-serialized strings."""
    model = _make_mock_model(
        id_val=id_val,
        bot_id=bot_id,
        engine_types=json.dumps(engine_types or [], ensure_ascii=False),
        share_policy=json.dumps(share_policy or {}, ensure_ascii=False),
        ext=json.dumps(ext or {}, ensure_ascii=False),
        **kwargs,
    )
    return model


# ==================== _model_to_record ====================


class TestModelToRecord:
    def test_converts_valid_model(self, repository):
        model = _make_mock_model_with_json(
            id_val=3,
            bot_id="bot-abc",
            bot_name="My Bot",
            entity_id="ent-1",
            status="online",
            engine_types=["openclaw"],
        )

        record = OrmAcBotRepository._model_to_record(model)

        assert isinstance(record, AcBotRecord)
        assert record.id == 3
        assert record.bot_id == "bot-abc"
        assert record.status == "online"
        assert record.engine_types == ["openclaw"]

    def test_none_row_returns_none(self, repository):
        assert OrmAcBotRepository._model_to_record(None) is None

    def test_all_required_fields(self, repository):
        now = datetime.now()
        model = _make_mock_model_with_json(
            id_val=5,
            bot_id="b1",
            bot_name="Name",
            bot_desc="Desc",
            entity_id="e1",
            entity_type="staff",
            creator_id="c1",
            owner_id="o1",
            engine_types=["a", "b"],
            status="online",
            binding_id=200,
            gmt_create=now,
            gmt_modified=now,
            modifier_id="m1",
            share_policy={"k": "v"},
            is_delete=0,
            active_engine="oc",
            device_id="d1",
            env="prod",
            owner_name="Owner",
            public="1",
            ext={"x": 1},
            bot_type="shared",
        )

        record = OrmAcBotRepository._model_to_record(model)

        assert record.id == 5
        assert record.bot_id == "b1"
        assert record.bot_name == "Name"
        assert record.bot_desc == "Desc"
        assert record.entity_id == "e1"
        assert record.entity_type == "staff"
        assert record.creator_id == "c1"
        assert record.owner_id == "o1"
        assert record.engine_types == ["a", "b"]
        assert record.status == "online"
        assert record.binding_id == 200
        assert record.gmt_create == now
        assert record.gmt_modified == now
        assert record.modifier_id == "m1"
        assert record.share_policy == {"k": "v"}
        assert record.is_delete == 0
        assert record.active_engine == "oc"
        assert record.device_id == "d1"
        assert record.env == "prod"
        assert record.owner_name == "Owner"
        assert record.public == "1"
        assert record.ext == {"x": 1}
        assert record.bot_type == "shared"

    def test_handles_none_json_fields(self, repository):
        model = _make_mock_model(
            engine_types=None,
            share_policy=None,
            ext=None,
        )

        record = OrmAcBotRepository._model_to_record(model)

        assert record.engine_types is None
        assert record.share_policy is None
        assert record.ext is None

    def test_handles_invalid_json(self, repository):
        model = _make_mock_model(
            engine_types="not-valid-json",
            share_policy="{malformed",
            ext="plain text",
        )

        record = OrmAcBotRepository._model_to_record(model)

        assert record.engine_types is None
        assert record.share_policy is None
        assert record.ext is None

    def test_handles_non_string_json_fields(self, repository):
        """When engine_types is already a list (not a string), return as-is."""
        model = _make_mock_model(engine_types=["already", "list"])
        model.engine_types = ["already", "list"]

        record = OrmAcBotRepository._model_to_record(model)

        assert record.engine_types == ["already", "list"]


# ==================== get_by_entity_id_bot_id_env ====================


class TestGetByEntityIdBotIdEnv:
    def test_found(self, repository, mock_session):
        model = _make_mock_model(
            id_val=5,
            entity_id="entity-xyz",
            bot_id="bot-xyz",
            env="prod",
            bot_name="My Bot",
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_entity_id_bot_id_env(
            entity_id="entity-xyz", bot_id="bot-xyz", env="prod"
        )

        assert result is not None
        assert result.id == 5
        assert result.bot_id == "bot-xyz"
        assert result.entity_id == "entity-xyz"
        assert result.env == "prod"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_entity_id_bot_id_env(
            entity_id="nonexistent", bot_id="nonexistent", env="dev"
        )

        assert result is None

    def test_uses_is_delete_filter(self, repository, mock_session):
        model = _make_mock_model()
        mock_session.query.return_value.filter.return_value.first.return_value = model

        repository.get_by_entity_id_bot_id_env(entity_id="e1", bot_id="b1", env="dev")

        mock_session.query.assert_called_once()


# ==================== get_active_by_entity_id_bot_id_env ====================


class TestGetActiveByEntityIdBotIdEnv:
    def test_found(self, repository, mock_session):
        model = _make_mock_model(
            id_val=8, entity_id="e2", bot_id="b2", env="dev", status="ACTIVE"
        )
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_active_by_entity_id_bot_id_env(
            entity_id="e2", bot_id="b2", env="dev"
        )

        assert result is not None
        assert result.id == 8
        assert result.status == "ACTIVE"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_active_by_entity_id_bot_id_env(
            entity_id="nonexistent", bot_id="nonexistent", env="dev"
        )

        assert result is None


# ==================== get_by_bot_id_env_exclude_default ====================


class TestGetByBotIdEnvExcludeDefault:
    def test_found(self, repository, mock_session):
        model = _make_mock_model(id_val=8, bot_id="bot-service", env="dev")
        mock_session.query.return_value.filter.return_value.first.return_value = model

        result = repository.get_by_bot_id_env_exclude_default(
            bot_id="bot-service", env="dev"
        )

        assert result is not None
        assert result.bot_id == "bot-service"

    def test_not_found(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = repository.get_by_bot_id_env_exclude_default(
            bot_id="nonexistent", env="dev"
        )

        assert result is None

    def test_rejects_default_bot_id(self, repository):
        with pytest.raises(ValueError, match="cannot be 'default'"):
            repository.get_by_bot_id_env_exclude_default(bot_id="default", env="dev")


# ==================== list_active_bots ====================


class TestListActiveBots:
    def test_returns_multiple(self, repository, mock_session):
        model1 = _make_mock_model(id_val=1, bot_id="bot-1")
        model2 = _make_mock_model(id_val=2, bot_id="bot-2")
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 2
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model1,
            model2,
        ]

        total, items = repository.list_active_bots(env="prod")

        assert total == 2
        assert len(items) == 2
        assert items[0].id == 1
        assert items[1].id == 2

    def test_empty_list(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 0
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        total, items = repository.list_active_bots(env="prod")

        assert total == 0
        assert items == []

    def test_with_bot_type_filter(self, repository, mock_session):
        model = _make_mock_model(id_val=1, bot_type="shared")
        mock_session.query.return_value.filter.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 1
        mock_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model
        ]

        total, items = repository.list_active_bots(env="prod", bot_type="shared")

        assert total == 1
        assert len(items) == 1

    def test_without_bot_type_filter(self, repository, mock_session):
        model = _make_mock_model()
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 1
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            model
        ]

        total, items = repository.list_active_bots(env="prod")

        assert total == 1
        assert len(items) == 1

    def test_with_pagination(self, repository, mock_session):
        models = [_make_mock_model(id_val=i) for i in range(1, 6)]
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 50
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = models

        total, items = repository.list_active_bots(env="prod", page=2, page_size=5)

        assert total == 50
        assert len(items) == 5

    def test_default_page_and_size(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 10
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [
            _make_mock_model()
        ]

        repository.list_active_bots()

        mock_session.query.return_value.filter.return_value.order_by.assert_called_once()
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.assert_called_once_with(
            0
        )
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.assert_called_once_with(
            20
        )

    def test_default_env_is_prod(self, repository, mock_session):
        mock_session.query.return_value.filter.return_value.with_entities.return_value.scalar.return_value = 0
        mock_session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        repository.list_active_bots()

        mock_session.query.assert_called_once()
