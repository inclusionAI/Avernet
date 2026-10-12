"""Atomic user-default and Bot-explicit MCP Header writes."""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.mcp import BotMCPConfig, UserMCPConfig
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
        BotMCPConfig.__table__.create(self.engine)
        self._sessions = sessionmaker(bind=self.engine)

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
