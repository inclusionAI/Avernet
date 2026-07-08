"""Database concern — community binding.

Capability: relational store connection. B3 binds the configured-URL
``CommunityDatabase``. The ``CommunityDatabaseConfig`` provider lives here
(community-only) and reads the ``database`` block of ``user_config``, with the
``DATABASE_URL`` env var taking precedence — corp/test never resolve it.
"""
from __future__ import annotations

import os

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


class CommunityDatabaseModule(Module):
    """community: SQLAlchemy DatabasePlugin over a configured URL."""

    @singleton
    @provider
    def database_config(self) -> cfg.CommunityDatabaseConfig:
        """Resolve the database URL: ``DATABASE_URL`` env wins, else the
        ``database`` block, else the dataclass default (a local SQLite file)."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("database")
        defaults = cfg.CommunityDatabaseConfig()
        url = os.environ.get("DATABASE_URL") or block.get("url") or defaults.url
        return cfg.CommunityDatabaseConfig(url=url)

    @singleton
    @provider
    @inject
    def database(self, config: cfg.CommunityDatabaseConfig) -> DatabasePlugin:
        from sqlalchemy.engine import make_url

        from agentclaw.community.plugins.community.database import CommunityDatabase

        # Mask any inline credentials — a Postgres/MySQL URL embeds the password
        # (postgresql://user:pass@host/db); never log it in the clear.
        try:
            safe_url = make_url(config.url).render_as_string(hide_password=True)
        except Exception:  # pragma: no cover — malformed URL surfaces on connect
            safe_url = "<unparseable>"
        logger.info("DatabasePlugin: CommunityDatabase (url=%s)", safe_url)
        return CommunityDatabase(config.url)
