"""Read-side contract for Bot-scoped MCP Manifest overrides."""

from __future__ import annotations

import json
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.mcp import BotMCPConfig
from agentclaw.community.core.repository.implementations.bot.bot_mcp_config import (
    BotMCPConfigRepository,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env


class _Database:
    def __init__(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        BotMCPConfig.__table__.create(engine)
        self._sessions = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _insert(db: _Database, *, tenant: str, bot_id: str, owner_id: str) -> None:
    with avernet_tenant_scope(tenant), db.orm_session() as session:
        session.add(
            BotMCPConfig(
                bot_id=bot_id,
                owner_id=owner_id,
                server_code="mcp.weather",
                config=json.dumps({"headers": {"X-Project": tenant}}),
                env=get_current_env(),
            )
        )


def test_reads_are_owner_bot_and_tenant_scoped() -> None:
    db = _Database()
    repo = BotMCPConfigRepository(db)
    _insert(db, tenant="tenant-a", bot_id="bot-a", owner_id="owner")
    _insert(db, tenant="tenant-b", bot_id="bot-b", owner_id="owner")

    with avernet_tenant_scope("tenant-a"):
        assert repo.get_by_bot_and_server_code(
            bot_id="bot-a", owner_id="owner", server_code="mcp.weather"
        ) == {"headers": {"X-Project": "tenant-a"}}
        assert repo.list_by_owner_and_server_code(
            owner_id="owner", server_code="mcp.weather"
        ) == {"bot-a": {"headers": {"X-Project": "tenant-a"}}}
        assert repo.get_by_bot_and_server_code(
            bot_id="bot-b", owner_id="owner", server_code="mcp.weather"
        ) is None
