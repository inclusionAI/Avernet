"""Template read eligibility, SQL short-circuiting, and unchanged writes."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.bot_management.services.template_service import (
    TemplateService,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.repository.implementations.bot.template import TemplateRepository
from agentclaw.community.plugin_api.models import BotModel


class Database:
    def __init__(self, engine):
        self.sessions = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        with self.sessions.begin() as session:
            yield session


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "dev")
    engine = create_engine("sqlite://")
    BotModel.__table__.create(engine)
    TemplateModel.__table__.create(engine)
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    db = Database(engine)
    repo = TemplateRepository(db)
    yield db, repo, statements
    engine.dispose()


def seed(world, bot_id="bot", engine="claude_code", *, env="dev", deleted=0):
    db, repo, _ = world
    with db.orm_session() as session:
        session.add(BotModel(
            bot_id=bot_id, entity_id="staff", entity_type="staff",
            creator_id="owner", owner_id="owner", active_engine=engine,
            env=env, is_delete=deleted,
        ))
    if not repo.exists_by_bot_id(bot_id):
        repo.insert({"bot_id": bot_id, "ext": {"architect_bot_id": "architect"}})


def template_selects(statements):
    return [sql for sql in statements
            if sql.lstrip().upper().startswith("SELECT") and "ac_templates" in sql]


@pytest.mark.parametrize("engine", ["claude_code", "aicoding"])
def test_allowed_engines_read_existing_template(world, engine):
    _, repo, statements = world
    seed(world, engine=engine)
    statements.clear()
    assert repo.get_by_bot_id("bot")["bot_id"] == "bot"
    assert repo.exists_by_bot_id("bot") is True
    assert [r["bot_id"] for r in repo.list_by_bot_ids(["bot", "bot"])] == ["bot"]
    assert [r["bot_id"] for r in repo.list_by_architect_bot_id("architect")] == ["bot"]
    assert len(template_selects(statements)) == 4


@pytest.mark.parametrize("engine", ["openclaw", "teclaw", "moltis", "hermes", "", "unknown"])
def test_other_engines_do_not_query_templates(world, engine):
    _, repo, statements = world
    seed(world, engine=engine)
    statements.clear()
    assert repo.get_by_bot_id("bot") is None
    assert template_selects(statements) == []


@pytest.mark.parametrize("case", ["missing", "deleted", "other_env"])
def test_ineligible_bot_does_not_expose_stale_template(world, case):
    _, repo, statements = world
    if case == "missing":
        repo.insert({"bot_id": "bot", "ext": {"architect_bot_id": "architect"}})
    else:
        seed(world, env="pre" if case == "other_env" else "dev",
             deleted=1 if case == "deleted" else 0)
    statements.clear()
    assert repo.get_by_bot_id("bot") is None
    assert not template_selects(statements)


def test_different_environment_cannot_enable_current_bot(world):
    _, repo, statements = world
    seed(world, engine="openclaw")
    seed(world, engine="claude_code", env="pre")
    statements.clear()
    assert repo.get_by_bot_id("bot") is None
    assert not template_selects(statements)


def test_batch_architect_and_exists_queries_do_not_filter_engines(world):
    _, repo, statements = world
    for bot_id, engine in [("cc", "claude_code"), ("ac", "aicoding"), ("other", "openclaw")]:
        seed(world, bot_id, engine)
    statements.clear()
    assert {r["bot_id"] for r in repo.list_by_bot_ids(["cc", "ac", "other", "missing", "cc"])} == {"cc", "ac", "other"}
    assert len(statements) == 1  # original direct template query, no Bot precheck
    assert len(template_selects(statements)) == 1
    assert {r["bot_id"] for r in repo.list_by_architect_bot_id("architect")} == {"cc", "ac", "other"}
    assert repo.list_by_architect_bot_id("different-architect") == []
    assert repo.exists_by_bot_id("other") is True
    assert not any("ac_bots" in sql for sql in statements)


def test_eligible_bot_without_template_returns_empty(world):
    _, repo, _ = world
    seed(world)
    repo.delete_by_bot_id("bot")
    assert repo.get_by_bot_id("bot") is None
    assert repo.exists_by_bot_id("bot") is False
    assert repo.list_by_bot_ids(["bot"]) == []


def test_engine_switch_is_not_cached(world):
    db, repo, statements = world
    seed(world)
    assert repo.get_by_bot_id("bot") is not None
    with db.orm_session() as session:
        session.query(BotModel).filter(BotModel.bot_id == "bot").update({"active_engine": "openclaw"})
    statements.clear()
    assert repo.get_by_bot_id("bot") is None
    assert not template_selects(statements)


def test_repository_writes_do_not_check_engine(world):
    _, repo, statements = world
    seed(world, engine="openclaw")
    statements.clear()
    assert repo.exists_by_bot_id("bot") is True
    assert repo.update_by_bot_id("bot", {"ext": {"name": "changed"}})["ext"] == {"name": "changed"}
    assert repo.delete_by_bot_id("bot") is True
    assert repo.insert({"bot_id": "bot", "ext": {"name": "new"}})["ext"] == {"name": "new"}
    assert not any("ac_bots" in sql for sql in statements)


def test_service_writes_use_physical_existence_not_read_visibility(world):
    _, repo, statements = world
    seed(world, engine="openclaw")
    service = TemplateService(repository=repo, vault=TokenVault(master_key=""))
    assert service.exists_template("bot") is True
    assert service.get_template_config_strict("bot") is None
    statements.clear()
    assert service.update_template("bot", {"name": "updated"})["ext"] == {"name": "updated"}
    assert service.create_or_update_template("bot", {"name": "upserted"})["ext"] == {"name": "upserted"}
    assert service.delete_template("bot") is True
    assert service.create_or_update_template("bot", {"name": "created"})["ext"] == {"name": "created"}
    assert not any("ac_bots" in sql for sql in statements)


def test_lookup_failures_remain_errors_in_strict_read(world, monkeypatch):
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
    assert service.exists_template("bot") is False
    assert service.list_templates_by_bot_ids(["bot"]) == []


def test_default_bot_detail_checks_the_requested_owner(world):
    from unittest.mock import Mock
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    db, repo, statements = world
    # Insert the eligible owner's row first, so an unscoped first() is unsafe.
    seed(world, bot_id="default", engine="claude_code")
    with db.orm_session() as session:
        session.add(BotModel(
            bot_id="default", entity_id="other", entity_type="staff",
            creator_id="other", owner_id="other", active_engine="openclaw",
            env="dev", is_delete=0,
        ))
    templates = TemplateService(repository=repo, vault=TokenVault(master_key=""))
    service = BotService.__new__(BotService)
    service._repository = Mock()
    service._template_service = templates
    service._repository.get_by_id_and_owner.return_value = {
        "bot_id": "default", "owner_id": "other", "active_engine": "openclaw",
    }
    statements.clear()
    result = service.get_bot("default", "other")
    service._repository.get_by_id_and_owner.assert_called_once_with("default", "other")
    assert "template_config" not in result
    assert not template_selects(statements)

    service._repository.get_by_id_and_owner.return_value = {
        "bot_id": "default", "owner_id": "owner", "active_engine": "claude_code",
    }
    assert service.get_bot("default", "owner")["template_config"] == {
        "architect_bot_id": "architect",
    }
    statements.clear()
    assert repo.get_by_bot_id("default", owner_id="missing") is None
    assert not template_selects(statements)


def test_other_queries_can_read_templates_without_any_bot(world):
    _, repo, statements = world
    repo.insert({"bot_id": "orphan", "ext": {"architect_bot_id": "architect"}})
    statements.clear()
    assert repo.exists_by_bot_id("orphan") is True
    assert [r["bot_id"] for r in repo.list_by_bot_ids(["orphan"])] == ["orphan"]
    assert [r["bot_id"] for r in repo.list_by_architect_bot_id("architect")] == ["orphan"]
    assert not any("ac_bots" in sql for sql in statements)
