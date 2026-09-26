"""Database concern — community binding.

Capability: relational store connection. B3 binds the configured-URL
``CommunityDatabase``. The ``CommunityDatabaseConfig`` provider lives here
(community-only) and reads the ``database`` block of ``user_config`` —
corp/test never resolve it.

The block's ``url`` is expected to carry an env placeholder
(``${DATABASE_URL:-sqlite:///./data/agentclaw.db}``); the YAML provider expands
it during config loading, which is where AGENTS.md puts raw environment access.
This module therefore reads no environment variable of its own.
"""
from __future__ import annotations

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
        """Read the ``database`` block, falling back to the dataclass defaults.

        Validates ``backend`` against the URL scheme rather than inferring one
        from the other: a deployment that flips ``backend`` to ``mysql`` and
        forgets to point ``url`` at the instance would otherwise boot happily on
        the default SQLite file and quietly write real traffic to a scratch file
        inside the container.
        """
        from agentclaw.community.di.modules.config_module import _block

        block = _block("database")
        defaults = cfg.CommunityDatabaseConfig()

        backend = str(block.get("backend") or defaults.backend).strip().lower()
        url = block.get("url") or defaults.url
        create_schema = block.get("create_schema")
        if create_schema is None:
            create_schema = defaults.create_schema

        expected_scheme = cfg.DATABASE_BACKEND_SCHEMES.get(backend)
        if expected_scheme is None:
            supported = ", ".join(sorted(cfg.DATABASE_BACKEND_SCHEMES))
            raise ValueError(
                f"Unknown database.backend {backend!r}; supported: {supported}"
            )
        if not url.startswith(expected_scheme):
            raise ValueError(
                f"database.backend is {backend!r} but database.url is not a "
                f"{expected_scheme} URL (got scheme {url.split(':', 1)[0]!r}); "
                "set both to the same store"
            )

        return cfg.CommunityDatabaseConfig(
            backend=backend, url=url, create_schema=bool(create_schema)
        )

    @singleton
    @provider
    @inject
    def database(self, config: cfg.CommunityDatabaseConfig) -> DatabasePlugin:
        from sqlalchemy.engine import make_url

        from agentclaw.community.plugins.community.database import CommunityDatabase

        # Mask any inline credentials — a MySQL/Postgres URL embeds the password
        # (mysql+pymysql://user:pass@host/db); never log it in the clear.
        try:
            safe_url = make_url(config.url).render_as_string(hide_password=True)
        except Exception:  # pragma: no cover — malformed URL surfaces on connect
            safe_url = "<unparseable>"
        logger.info(
            "DatabasePlugin: CommunityDatabase (backend=%s, url=%s, create_schema=%s)",
            config.backend,
            safe_url,
            config.create_schema,
        )
        return CommunityDatabase(config.url, create_schema=config.create_schema)
