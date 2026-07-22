"""Tests for plugin_api/models.py BotModel."""
import json
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import BotModel


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