"""Database plugin lifecycle wiring for the gateway composition root."""

from __future__ import annotations

from sqlalchemy.orm import Session

from gateway.community.spi.database import DataSourcePlugin

from ._configs import DatabaseConfig


def _seed_bare_data(session: Session) -> None:
    """Seed deterministic bare-mode authn rows.

    These rows are community/dev defaults for DB-backed auth strategies.
    Idempotent so repeated ``init_database`` calls are safe. Lives in the
    composition root (not the plugin) because it references core ORM models.
    """
    from datetime import datetime

    from gateway.community.core.access_key import AccessKeyRow
    from gateway.community.core.app import AppRow
    from gateway.community.core.bot import BotRow

    if session.get(BotRow, 1) is None:
        session.add(
            BotRow(
                id=1,
                session_token="bot-key",
                bot_uuid="bot-7",
                env="dev",
                created_by="owner-1",
                agent_code="agent-1",
            )
        )
    if session.get(AppRow, 1) is None:
        session.add(
            AppRow(
                id=1,
                token="app-key",
                app_name="Demo App",
                app_type="assistant",
                owners="org-1",
                tenant="t",
                status="ACTIVE",
                env="dev",
                config={},
            )
        )
    if session.get(AccessKeyRow, 1) is None:
        session.add(
            AccessKeyRow(
                id=1,
                token="ak-token",
                access_key="ak-1",
                tenant="t",
                expire_at=datetime(2027, 1, 1, 0, 0, 0),
            )
        )


def initialize_database(
    db_plugin: DataSourcePlugin,
    config: DatabaseConfig,
) -> DataSourcePlugin:
    """Initialise the DI-resolved database plugin and return it.

    The database implementation is selected by ``PluginContainer``. This helper
    only applies the already-loaded configuration to that resolved plugin and
    seeds bare-mode data; it never constructs a concrete database implementation
    itself.
    """
    db_plugin.init_database(config)

    # Seed bare-mode authn rows after schema creation. The plugin's own
    # ``seed`` is a no-op because it cannot import core ORM models (layer rule:
    # plugins must not import core). The composition root has no such ban.
    with db_plugin.orm_session() as session:
        _seed_bare_data(session)

    return db_plugin
