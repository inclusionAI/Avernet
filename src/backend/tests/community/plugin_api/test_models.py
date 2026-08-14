"""Tests for plugin_api/models.py BotModel."""
import json
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import BotModel, ResourceModel


class _TestDB:
    """Minimal test database fixture."""
    def __init__(self, engine):
        self._factory = sessionmaker(
            bind=engine, autocommit=False, autoflush=False
        )

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def db(tmp_path):
    """Create a test database with BotModel table."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_bots.db'}",
        connect_args={"check_same_thread": False},
    )
    BotModel.__table__.create(engine)
    return _TestDB(engine)


@pytest.fixture
def resource_db(tmp_path):
    """Create a test database with ResourceModel table."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_resources.db'}",
        connect_args={"check_same_thread": False},
    )
    ResourceModel.__table__.create(engine)
    return _TestDB(engine)


class TestBotModelToDict:
    """Tests for BotModel.to_dict method."""

    def test_to_dict_includes_call_type_and_caller_config_revision(self, db):
        """Test that to_dict includes call_type and caller_config_revision fields."""
        # Create a BotModel instance with default values
        bot = BotModel(
            bot_id="test-bot",
            bot_name="Test Bot",
            bot_desc="Test description",
            entity_id="entity-1",
            entity_type="staff",
            creator_id="user-1",
            owner_id="user-1",
            owner_name="Test User",
            status="ACTIVE",
        )

        # Insert into database to trigger default values
        with db.orm_session() as session:
            session.add(bot)
            session.flush()
            session.refresh(bot)
            result = bot.to_dict()

        # Verify call_type and caller_config_revision are present
        assert "call_type" in result
        assert "caller_config_revision" in result

        # Verify default values (from database)
        assert result["call_type"] == "owner"
        assert result["caller_config_revision"] == 0

    def test_to_dict_with_custom_call_type(self, db):
        """Test that to_dict includes custom call_type value."""
        bot = BotModel(
            bot_id="test-bot",
            bot_name="Test Bot",
            bot_desc="Test description",
            entity_id="entity-1",
            entity_type="staff",
            creator_id="user-1",
            owner_id="user-1",
            owner_name="Test User",
            status="ACTIVE",
            call_type="caller",
            caller_config_revision=5,
        )

        with db.orm_session() as session:
            session.add(bot)
            session.flush()
            session.refresh(bot)
            result = bot.to_dict()

        # Verify custom values
        assert result["call_type"] == "caller"
        assert result["caller_config_revision"] == 5

    def test_to_dict_includes_all_required_fields(self, db):
        """Test that to_dict includes all required fields."""
        bot = BotModel(
            bot_id="test-bot",
            bot_name="Test Bot",
            bot_desc="Test description",
            entity_id="entity-1",
            entity_type="staff",
            creator_id="user-1",
            owner_id="user-1",
            owner_name="Test User",
            status="ACTIVE",
        )

        with db.orm_session() as session:
            session.add(bot)
            session.flush()
            session.refresh(bot)
            result = bot.to_dict()

        # Verify all essential fields are present
        required_fields = [
            "id",
            "bot_id",
            "bot_name",
            "bot_desc",
            "entity_id",
            "entity_type",
            "creator_id",
            "owner_id",
            "owner_name",
            "engine_types",
            "active_engine",
            "status",
            "binding_id",
            "device_id",
            "gmt_create",
            "gmt_modified",
            "modifier_id",
            "share_policy",
            "is_delete",
            "public",
            "ext",
            "env",
            "bot_type",
            "template_type",
            "call_type",
            "caller_config_revision",
        ]

        for field in required_fields:
            assert field in result, f"Field '{field}' is missing from to_dict result"

    def test_to_dict_key_set_unchanged_by_avernet_tenant(self, db):
        """The avernet_tenant column must NOT surface in to_dict().

        Isolation adds a column but must not change any current internal API
        response body — so to_dict()'s exact key set stays what it was before
        this feature. Pinning the full set here fails loudly if avernet_tenant
        (or anything else) ever leaks in.
        """
        expected_keys = {
            "id",
            "bot_id",
            "bot_name",
            "bot_desc",
            "entity_id",
            "entity_type",
            "creator_id",
            "owner_id",
            "owner_name",
            "engine_types",
            "active_engine",
            "status",
            "binding_id",
            "device_id",
            "gmt_create",
            "gmt_modified",
            "modifier_id",
            "share_policy",
            "is_delete",
            "public",
            "ext",
            "env",
            "bot_type",
            "template_type",
            "call_type",
            "caller_config_revision",
        }

        bot = BotModel(
            bot_id="test-bot",
            bot_name="Test Bot",
            bot_desc="Test description",
            entity_id="entity-1",
            entity_type="staff",
            creator_id="user-1",
            owner_id="user-1",
            owner_name="Test User",
            status="ACTIVE",
        )

        with db.orm_session() as session:
            session.add(bot)
            session.flush()
            session.refresh(bot)
            # The column exists and is populated on the row...
            assert bot.avernet_tenant == "teamclaw"
            result = bot.to_dict()

        # ...but it is absent from the serialized form, and the full key set is
        # exactly what it was before isolation.
        assert "avernet_tenant" not in result
        assert set(result.keys()) == expected_keys

    def test_to_dict_handles_json_fields(self, db):
        """Test that to_dict properly serializes JSON fields."""
        bot = BotModel(
            bot_id="test-bot",
            bot_name="Test Bot",
            bot_desc="Test description",
            entity_id="entity-1",
            entity_type="staff",
            creator_id="user-1",
            owner_id="user-1",
            owner_name="Test User",
            status="ACTIVE",
            engine_types=json.dumps(["openclaw", "teclaw"]),
            share_policy=json.dumps({"policy": "test"}),
            ext=json.dumps({"custom": "data"}),
        )

        with db.orm_session() as session:
            session.add(bot)
            session.flush()
            session.refresh(bot)
            result = bot.to_dict()

        # Verify JSON fields are properly deserialized
        assert isinstance(result["engine_types"], list)
        assert result["engine_types"] == ["openclaw", "teclaw"]
        assert isinstance(result["share_policy"], dict)
        assert result["share_policy"] == {"policy": "test"}
        assert isinstance(result["ext"], dict)
        assert result["ext"] == {"custom": "data"}


class TestResourceModelToDict:
    """Tests for ResourceModel.to_dict method."""

    def test_resource_to_dict_excludes_tenant(self, resource_db):
        """The avernet_tenant column must NOT surface in ResourceModel.to_dict().

        Mirrors the BotModel key-set pin (test_to_dict_key_set_unchanged_by_avernet_tenant):
        isolation adds a column to ac_resource but must not change any
        current internal API response body — so to_dict()'s exact key set
        stays what it was before this feature. Pinning the full set here
        fails loudly if avernet_tenant (or anything else) ever leaks in.
        """
        expected_keys = {
            "id",
            "name",
            "resource_type",
            "status",
            "gmt_created",
            "gmt_modified",
            "attributes",
            "metadata",
            "user_id",
            "created_by",
            "source",
            "bolt_id",
            "env",
        }

        resource = ResourceModel(
            name="test-resource",
            resource_type="file",
        )

        with resource_db.orm_session() as session:
            session.add(resource)
            session.flush()
            session.refresh(resource)
            # The column exists and is populated on the row (server_default
            # backfill, same as BotModel)...
            assert resource.avernet_tenant == "teamclaw"
            result = resource.to_dict()

        # ...but it is absent from the serialized form, and the full key set
        # is exactly what it was before isolation.
        assert "avernet_tenant" not in result
        assert set(result.keys()) == expected_keys