"""Conformance of the atomic scoped-MCP repository contract."""

from contextlib import contextmanager

from sqlalchemy import create_engine
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
from agentclaw.community.core.repository.protocols.bot.mcp import (
    ScopedMCPConfigRepositoryProtocol,
)


def test_atomic_replace_contract_updates_both_scopes_and_clears_bot_rules() -> None:
    engine = create_engine("sqlite:///:memory:")
    UserMCPConfig.__table__.create(engine)
    BotMCPConfig.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    class Database:
        @contextmanager
        def transactional_orm_session(self):
            session = sessions()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        orm_session = transactional_orm_session

    db = Database()
    subject: ScopedMCPConfigRepositoryProtocol = ScopedMCPConfigRepository(db)
    assert isinstance(subject, ScopedMCPConfigRepositoryProtocol)

    subject.replace(
        user_id="owner", server_code="mcp.weather", headers={"B": "2"},
        bot_headers={"bot-x": {"A": "3"}}, owned_bot_ids={"bot-x"},
        endpoint_env="PROD", transport_protocol="SSE",
    )
    user = UserMCPConfigRepository(db).get_by_user_and_server_code(
        "owner", "mcp.weather"
    )
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert user["extra_config"]["headers"] == {"B": "2"}
    assert bot["headers"] == {"A": "3"}

    subject.replace(
        user_id="owner", server_code="mcp.weather", headers={},
        bot_headers={}, owned_bot_ids={"bot-x"}, endpoint_env="PROD",
        transport_protocol=None,
    )
    bot = BotMCPConfigRepository(db).get_by_bot_and_server_code(
        owner_id="owner", bot_id="bot-x", server_code="mcp.weather"
    )
    assert bot is None
