"""TestingDatabaseModule — SQLite database binding for test/singlebox.

Installed by ``modules_for`` for the ``test`` and ``singlebox`` profiles
(LOCAL stubs + SQLite), alongside :class:`TestingInfrastructureModule`.
The ``corp`` profile installs the ZDAS database binding instead
(``ZdasDatabaseModule``); no profile mixes the two.
"""
from __future__ import annotations


from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin


logger = get_logger()


class TestingDatabaseModule(Module):
    """Override ``DatabasePlugin`` with the local SQLite implementation."""

    @singleton
    @provider
    def database(self) -> DatabasePlugin:
        from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests

        # The local SqliteDB uses a module-level engine singleton. Reset
        # it so each injector build gets a fresh in-memory database —
        # required for per-test isolation when a test fixture rebuilds
        # the injector. Safe to call unconditionally (idempotent).
        reset_for_tests()
        import os as _os
        _db_url = _os.environ.get("DATABASE_URL", "sqlite:///:memory:")
        logger.info("DatabasePlugin: SqliteDB (testing override, url=%s)", _db_url)
        return SqliteDB()
