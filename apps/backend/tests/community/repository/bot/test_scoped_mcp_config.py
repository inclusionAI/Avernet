"""Atomic user-default and Bot-explicit MCP Header writes."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
from agentclaw.community.core.mcp.errors import McpConfigValueError
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
    BotMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.scoped_mcp_config import (
    ScopedMCPConfigRepository,
)
from agentclaw.community.core.repository.implementations.bot.user_mcp_config import (
    UserMCPConfigRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env


class _Database:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        UserMCPConfig.__table__.create(self.engine)
        BotModel.__table__.create(self.engine)
        BotMCPConfig.__table__.create(self.engine)
        self._sessions = sessionmaker(bind=self.engine)
        with self.transactional_orm_session() as session:
            for bot_id in ("bot-x", "bot-y"):
                session.add(BotModel(bot_id=bot_id, entity_id="owner", entity_type="staff",
                                     creator_id="owner", owner_id="owner", env=get_current_env()))

    @contextmanager
    def transactional_orm_session(self):
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    orm_session = transactional_orm_session


def test_replace_preserves_unrelated_fields_and_clears_omitted_bot_headers() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                UserMCPConfig(
                    user_id="owner",
                    server_code="mcp.weather",
                    env=get_current_env(),
                    api_key="existing-secret",
                    extra_config='{"api_key":"existing-secret","headers":{"B":"2"},"endpoint_env":"PRE","transport_protocol":"SSE","url":"https://global.example.test/mcp"}',
                ),
                BotMCPConfig(
                    owner_id="owner", bot_id="bot-x", server_code="mcp.weather",
                    env=get_current_env(),
                    config='{"headers":{"A":"old"},"url":"https://example.test/mcp"}',
                ),
                BotMCPConfig(
                    owner_id="owner", bot_id="bot-y", server_code="mcp.weather",
                    env=get_current_env(), config='{"headers":{"C":"old"}}',
                ),
            ]
        )

    ScopedMCPConfigRepository(db).replace(
        user_id="owner",
        server_code="mcp.weather",
        headers={"B": "4"},
        bot_headers={"bot-x": {"A": "3"}},
        owned_bot_ids={"bot-x", "bot-y"},
        endpoint_env="PROD",
        transport_protocol=None,
    )

    user = UserMCPConfigRepository(db).get_by_user_and_server_code("owner", "mcp.weather")
    bots = BotMCPConfigRepository(db).list_by_owner_and_server_code(
        owner_id="owner", server_code="mcp.weather"
    )
    assert user["api_key"] == "existing-secret"
    assert user["extra_config"] == {
        "api_key": "existing-secret",
        "headers": {"B": "4"},
        "endpoint_env": "PROD",
        "transport_protocol": None,
        "url": "https://global.example.test/mcp",
    }
    assert bots == {"bot-x": {"headers": {"A": "3"}, "url": "https://example.test/mcp"}}

    ScopedMCPConfigRepository(db).replace(
        user_id="owner", server_code="mcp.weather", headers={}, bot_headers={},
        owned_bot_ids={"bot-x", "bot-y"}, endpoint_env="PROD",
        transport_protocol=None,
    )
    cleared_user = UserMCPConfigRepository(db).get_by_user_and_server_code(
        "owner", "mcp.weather"
    )
    cleared_bots = BotMCPConfigRepository(db).list_by_owner_and_server_code(
        owner_id="owner", server_code="mcp.weather"
    )
    assert cleared_user["extra_config"]["headers"] == {}
    assert cleared_user["extra_config"]["url"] == "https://global.example.test/mcp"
    assert cleared_user["api_key"] == "existing-secret"
    assert cleared_bots == {"bot-x": {"url": "https://example.test/mcp"}}


def test_url_snapshot_replaces_only_url_fields_and_empty_clears_them() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all([
            UserMCPConfig(
                user_id="owner", server_code="mcp.weather", env=get_current_env(),
                extra_config='{"headers":{"B":"2"},"url":"https://old.example.test/mcp"}',
            ),
            BotMCPConfig(
                owner_id="owner", bot_id="bot-x", server_code="mcp.weather",
                env=get_current_env(),
                config='{"headers":{"A":"3"},"url":"https://old-bot.example.test/mcp"}',
            ),
        ])

    repo = ScopedMCPConfigRepository(db)
    common = {
        "user_id": "owner", "server_code": "mcp.weather",
        "headers": {"B": "2"}, "bot_headers": {"bot-x": {"A": "3"}},
        "owned_bot_ids": {"bot-x"}, "endpoint_env": "PROD",
        "transport_protocol": None,
    }
    repo.replace(
        **common, user_url="https://new.example.test/mcp",
        bot_urls={"bot-x": "https://new-bot.example.test/mcp"},
    )
    user = UserMCPConfigRepository(db).get_by_user_and_server_code("owner", "mcp.weather")
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert user["extra_config"]["url"] == "https://new.example.test/mcp"
    assert bot == {
        "headers": {"A": "3"}, "url": "https://new-bot.example.test/mcp",
    }

    repo.replace(**common, user_url=None, bot_urls={})
    user = UserMCPConfigRepository(db).get_by_user_and_server_code("owner", "mcp.weather")
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert "url" not in user["extra_config"]
    assert bot == {"headers": {"A": "3"}}


def test_replace_rolls_back_user_and_bot_changes_when_second_write_fails() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                UserMCPConfig(
                    user_id="owner", server_code="mcp.weather", env=get_current_env(),
                    extra_config='{"headers":{"B":"2"},"endpoint_env":"PROD","url":"https://old.example.test/mcp"}',
                ),
                BotMCPConfig(
                    owner_id="owner", bot_id="bot-x", server_code="mcp.weather",
                    env=get_current_env(),
                    config='{"headers":{"A":"old"},"url":"https://old-bot.example.test/mcp"}',
                ),
            ]
        )

    writes = 0

    def fail_on_second_write(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal writes
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes += 1
            if writes == 2:
                raise RuntimeError("injected second DB write failure")

    event.listen(db.engine, "before_cursor_execute", fail_on_second_write)
    try:
        with pytest.raises(RuntimeError, match="second DB write failure"):
            ScopedMCPConfigRepository(db).replace(
                user_id="owner", server_code="mcp.weather", headers={"B": "4"},
                bot_headers={"bot-x": {"A": "3"}}, owned_bot_ids={"bot-x"},
                endpoint_env="PRE", transport_protocol="SSE",
                user_url="https://new.example.test/mcp",
                bot_urls={"bot-x": "https://new-bot.example.test/mcp"},
            )
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_on_second_write)

    user = UserMCPConfigRepository(db).get_by_user_and_server_code("owner", "mcp.weather")
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert writes == 2
    assert user["extra_config"]["headers"] == {"B": "2"}
    assert user["extra_config"]["url"] == "https://old.example.test/mcp"
    assert bot["headers"] == {"A": "old"}
    assert bot["url"] == "https://old-bot.example.test/mcp"


def test_replace_is_tenant_scoped_for_both_user_and_bot_rows() -> None:
    db = _Database()
    for tenant in ("tenant-a", "tenant-b"):
        with avernet_tenant_scope(tenant), db.transactional_orm_session() as session:
            session.add(BotModel(bot_id="bot-x", entity_id="owner", entity_type="staff",
                                 creator_id="owner", owner_id="owner", env=get_current_env()))
            session.add_all(
                [
                    UserMCPConfig(
                        user_id="owner", server_code="mcp.weather",
                        env=get_current_env(), extra_config='{"headers":{"B":"old"}}',
                    ),
                    BotMCPConfig(
                        owner_id="owner", bot_id="bot-x", server_code="mcp.weather",
                        env=get_current_env(), config='{"headers":{"A":"old"}}',
                    ),
                ]
            )

    with avernet_tenant_scope("tenant-a"):
        ScopedMCPConfigRepository(db).replace(
            user_id="owner", server_code="mcp.weather", headers={"B": "new"},
            bot_headers={"bot-x": {"A": "new"}}, owned_bot_ids={"bot-x"},
            endpoint_env="PROD", transport_protocol="SSE",
        )

    with avernet_tenant_scope("tenant-b"):
        user = UserMCPConfigRepository(db).get_by_user_and_server_code(
            "owner", "mcp.weather"
        )
        bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
            owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
        )
        assert user["extra_config"]["headers"] == {"B": "old"}
        assert bot["headers"] == {"A": "old"}


def test_empty_header_value_is_stored_as_an_explicit_rule() -> None:
    db = _Database()

    ScopedMCPConfigRepository(db).replace(
        user_id="owner", server_code="mcp.weather", headers={"B": ""},
        bot_headers={"bot-x": {"A": ""}}, owned_bot_ids={"bot-x"},
        endpoint_env="PROD", transport_protocol=None,
    )

    user = UserMCPConfigRepository(db).get_by_user_and_server_code(
        "owner", "mcp.weather"
    )
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert user["extra_config"]["headers"] == {"B": ""}
    assert bot["headers"] == {"A": ""}


def test_patch_bot_merges_overrides_without_touching_other_fields() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        bot = session.query(BotModel).filter_by(bot_id="bot-x").one()
        bot_pk = bot.id
        session.add(BotMCPConfig(
            owner_id="owner", bot_id="bot-x", server_code="weather",
            env=get_current_env(),
            config='{"headers":{"X-Region":"old","Keep":"yes"},"url":"https://example.invalid/mcp","endpoint_env":"PRE"}',
        ))

    repo = ScopedMCPConfigRepository(db)
    address = dict(bot_pk=bot_pk, bot_id="bot-x", entity_id="owner",
                   current_owner_id="owner", server_code="weather")
    patch = dict(headers={"x-region": None, "Empty": ""})
    saved, changed = repo.patch_bot(**address, patch=patch)
    assert changed
    assert saved == {
        "headers": {"Keep": "yes", "Empty": ""},
        "url": "https://example.invalid/mcp",
        "endpoint_env": "PRE",
    }
    assert repo.patch_bot(**address, patch=patch) == (saved, False)
    assert repo.patch_bot(**address, patch={"url": None}) == (
        {"headers": {"Keep": "yes", "Empty": ""}, "endpoint_env": "PRE"}, True
    )


def test_patch_only_creates_a_row_for_an_effective_override() -> None:
    db = _Database()
    with db.transactional_orm_session() as session:
        bot_pk = session.query(BotModel).filter_by(bot_id="bot-x").one().id
    repo = ScopedMCPConfigRepository(db)
    address = dict(bot_pk=bot_pk, bot_id="bot-x", entity_id="owner",
                   current_owner_id="owner", server_code="weather")
    assert repo.patch_bot(**address, patch={"url": None, "headers": {}}) == ({}, False)
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).count() == 0

    patch = {"url": "https://bot.example.invalid/mcp"}
    assert repo.patch_bot(**address, patch=patch) == (patch, True)
    assert repo.patch_bot(**address, patch=patch) == (patch, False)
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).one().bot_id == "bot-x"


def test_patch_storage_failure_does_not_expose_bound_connection_settings() -> None:
    from traceback import format_exception

    from sqlalchemy.exc import StatementError

    db = _Database()
    with db.orm_session() as session:
        bot_pk = session.query(BotModel).filter_by(bot_id="bot-x").one().id

    @event.listens_for(db.engine, "before_cursor_execute")
    def refuse_write(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("INSERT INTO ac_bot_mcp_config"):
            raise StatementError("synthetic write failure", statement, parameters, None)

    url = "https://bot.example.invalid/mcp?example=private"
    with pytest.raises(RuntimeError, match="Bot MCP config persistence failed") as exc:
        ScopedMCPConfigRepository(db).patch_bot(
            bot_pk=bot_pk, bot_id="bot-x", entity_id="owner", current_owner_id="owner",
            server_code="weather", patch={"url": url, "headers": {"X-Sample": "sensitive-fixture"}},
        )
    logged = "".join(format_exception(exc.value))
    assert url not in logged
    assert "sensitive-fixture" not in logged
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).count() == 0


def test_patch_refuses_the_same_bot_identity_under_another_tenant() -> None:
    db = _Database()
    with db.orm_session() as session:
        bot_pk = session.query(BotModel).filter_by(bot_id="bot-x").one().id
    with avernet_tenant_scope("test-other"):
        with pytest.raises(McpConfigValueError, match="Bot address changed"):
            ScopedMCPConfigRepository(db).patch_bot(
                bot_pk=bot_pk, bot_id="bot-x", entity_id="owner", current_owner_id="owner",
                server_code="weather", patch={"headers": {"X-Sample": "override"}},
            )
    with db.orm_session() as session:
        assert session.query(BotMCPConfig).count() == 0


def test_effective_projection_work_commits_with_scoped_bot_and_inherited_overrides() -> None:
    from agentclaw.community.core.models.capability_projection import BotCapabilityProjection
    from agentclaw.community.core.repository.implementations.skill_center.capability_projection import (
        CapabilityProjectionRepository,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import ProjectionScope

    db = _Database()
    BotCapabilityProjection.__table__.create(db.engine)
    projections = CapabilityProjectionRepository(db)
    base = projections.require(bot_id="bot-x", entity_id="owner", scope=ProjectionScope(skills=True))
    repo = ScopedMCPConfigRepository(db)
    with db.orm_session() as session:
        bot_pk = session.query(BotModel).filter_by(bot_id="bot-x").one().id
    address = dict(bot_pk=bot_pk, bot_id="bot-x", entity_id="owner",
                   current_owner_id="owner", server_code="weather")

    saved, changed = repo.patch_bot(**address, patch={"headers": {"X-Region": "sample"}},
                                    create_projection=False)
    assert changed and saved == {"headers": {"X-Region": "sample"}}
    override = projections.read(bot_id="bot-x", entity_id="owner")
    assert override.version == base.version + 1
    assert override.scope.updated_mcp == {"weather"}

    tracked = repo.replace(
        user_id="owner", server_code="weather", headers={"X-Region": "new"},
        bot_headers={}, owned_bot_ids={"bot-x", "bot-y"},
        endpoint_env="PROD", transport_protocol=None, create_projection=False,
    )
    assert tracked == {"bot-x": "owner"}
    assert projections.read(bot_id="bot-x", entity_id="owner").version == override.version + 1
    assert projections.read(bot_id="bot-y", entity_id="owner") is None
