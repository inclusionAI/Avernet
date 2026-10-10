"""Only the shared default Bot is excluded from single-template reads."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.services.template_service import TemplateService
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.implementations.bot.template import TemplateRepository


class Database:
    def __init__(self, engine):
        self.sessions = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        with self.sessions.begin() as session:
            yield session


@pytest.fixture
def world():
    engine = create_engine("sqlite://")
    # No ac_bots table: template lookup must not depend on Bot metadata.
    TemplateModel.__table__.create(engine)
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    db = Database(engine)
    yield db, TemplateRepository(db), statements
    engine.dispose()


def test_default_returns_none_without_opening_a_database_session():
    db = Mock()
    db.orm_session.side_effect = AssertionError("default must not access the database")
    repo = TemplateRepository(db)
    assert repo.get_by_bot_id("default") is None
    db.orm_session.assert_not_called()


@pytest.mark.parametrize("has_template", [True, False])
def test_default_is_hidden_even_when_a_template_exists(world, has_template):
    _, repo, statements = world
    if has_template:
        repo.insert({"bot_id": "default", "ext": {"name": "legacy"}})
    statements.clear()
    service = TemplateService(repository=repo, vault=TokenVault(master_key=""))
    assert service.get_template("default") is None
    assert service.get_template_config("default") is None
    assert service.get_template_config_strict("default") is None
    assert statements == []


@pytest.mark.parametrize("bot_id", ["bot", "default-1", "Default", "default "])
def test_non_default_reads_directly_without_bot_metadata(world, bot_id):
    _, repo, statements = world
    repo.insert({"bot_id": bot_id, "ext": {"name": "existing"}})
    statements.clear()
    assert repo.get_by_bot_id(bot_id)["ext"] == {"name": "existing"}
    assert len(statements) == 1
    assert "ac_templates" in statements[0]
    assert "ac_bots" not in statements[0]


def test_missing_non_default_template_returns_none(world):
    _, repo, statements = world
    assert repo.get_by_bot_id("missing") is None
    assert len(statements) == 1


def test_batch_architect_and_exists_queries_keep_default(world):
    _, repo, statements = world
    for bot_id in ["default", "other"]:
        repo.insert({"bot_id": bot_id, "ext": {"architect_bot_id": "architect"}})
    statements.clear()
    assert repo.exists_by_bot_id("default") is True
    assert {r["bot_id"] for r in repo.list_by_bot_ids(["default", "other"])} == {"default", "other"}
    assert {r["bot_id"] for r in repo.list_by_architect_bot_id("architect")} == {"default", "other"}
    assert len(statements) == 3
    assert not any("ac_bots" in sql for sql in statements)


def test_writes_keep_default_records(world):
    _, repo, _ = world
    service = TemplateService(repository=repo, vault=TokenVault(master_key=""))
    assert service.create_template("default", {"name": "created"})["ext"] == {"name": "created"}
    assert service.update_template("default", {"name": "updated"})["ext"] == {"name": "updated"}
    assert service.create_or_update_template("default", {"name": "upserted"})["ext"] == {"name": "upserted"}
    assert service.delete_template("default") is True
    assert service.create_or_update_template("default", {"name": "new"})["ext"] == {"name": "new"}


@pytest.mark.parametrize("owner_id", ["owner", "other"])
def test_default_bot_detail_never_attaches_template(world, owner_id):
    _, repo, statements = world
    repo.insert({"bot_id": "default", "ext": {"name": "legacy"}})
    service = BotService.__new__(BotService)
    service._repository = Mock()
    service._template_service = TemplateService(repository=repo, vault=TokenVault(master_key=""))
    service._repository.get_by_id_and_owner.return_value = {
        "bot_id": "default", "owner_id": owner_id, "active_engine": "claude_code",
    }
    statements.clear()
    result = service.get_bot("default", owner_id)
    service._repository.get_by_id_and_owner.assert_called_once_with("default", owner_id)
    assert "template_config" not in result
    assert statements == []


def test_non_default_query_failures_keep_strict_and_lenient_semantics(world, monkeypatch):
    db, repo, _ = world
    service = TemplateService(repository=repo, vault=TokenVault(master_key=""))

    @contextmanager
    def fail():
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(db, "orm_session", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        repo.get_by_bot_id("bot")
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.get_template_config_strict("bot")
    assert service.get_template("bot") is None
