"""Bootstrap DI container test fixtures.

Sets PLUGIN_DATABASE=SQLITE_ORM and initialises an in-memory SQLite
database so that the container chain resolves to ORM repository
variants without any external ZDAS/Layotto dependency.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

# Set the env var so init_database() and containers resolve to
# SQLite ORM in tests.
os.environ["PLUGIN_DATABASE"] = "SQLITE_ORM"

# All 17 ORM model modules must be imported so that
# SqliteOrmPlugin.create_all() discovers every table.
import secbaas.core.repository.ac_bot._orm_model  # noqa: F401
import secbaas.core.repository.ac_bot_publish._orm_model  # noqa: F401
import secbaas.core.repository.api_gateway._orm_model  # noqa: F401
import secbaas.core.repository.bot._orm_model  # noqa: F401
import secbaas.core.repository.bot_device_rel._orm_model  # noqa: F401
import secbaas.core.repository.bot_run._orm_model  # noqa: F401
import secbaas.core.repository.bot_session._orm_model  # noqa: F401
import secbaas.core.repository.device._orm_model  # noqa: F401
import secbaas.core.repository.device_binding._orm_model  # noqa: F401
import secbaas.core.repository.device_template._orm_model  # noqa: F401
import secbaas.core.repository.distributed_lock._orm_model  # noqa: F401
import secbaas.core.repository.local_user_machine._orm_model  # noqa: F401
import secbaas.core.repository.publish._orm_model  # noqa: F401
import secbaas.core.repository.publish_batch._orm_model  # noqa: F401
import secbaas.core.repository.publish_record._orm_model  # noqa: F401
import secbaas.core.repository.system_config._orm_model  # noqa: F401
import secbaas.core.repository.tenant._orm_model  # noqa: F401
from secbaas.core.database import db_manager as _global_db_manager
from secbaas.plugins.database.stub.sqlite_orm import SqliteOrmPlugin


@pytest.fixture(autouse=True)
def _inject_db_config() -> None:
    """Reset container singleton and ensure plugin_database config is set.

    Tests in other modules (e.g., test_app_cron.py) may pollute the
    module-level singleton in secbaas.bootstrap.  This fixture creates
    a fresh container and injects the SQLITE_ORM config before each test,
    then restores the original singleton afterwards.
    """
    import secbaas.bootstrap as _mod
    from secbaas.bootstrap import get_container

    # Save and null the singleton so get_container() creates a fresh one
    original = _mod._container
    _mod._container = None

    get_container().config.from_dict(
        {
            "plugins": {
                "database": {
                    "plugin_database": os.environ.get("PLUGIN_DATABASE", "ZDAS_ORM"),
                },
            },
        }
    )
    yield
    _mod._container = original


@pytest.fixture(scope="module")
def sqlite_backend() -> Generator[SqliteOrmPlugin, None, None]:
    """Create and seed an in-memory SQLite database.

    Discovers all ORM table definitions via the imports above and
    inserts required seed data (tenant, template).
    """
    plugin = SqliteOrmPlugin("sqlite:///:memory:")
    plugin.create_all()
    yield plugin
    plugin._sync_engine.dispose()


@pytest.fixture(autouse=True)
def _init_db_manager(
    sqlite_backend: SqliteOrmPlugin,
) -> Generator[None, None, None]:
    """Inject the SQLite plugin into the global db_manager singleton.

    This must happen before any repository is resolved via the container.
    The fixture restores the original plugin state after each test to
    avoid cross-test pollution.
    """
    original_plugin = _global_db_manager._plugin
    _global_db_manager.init_plugin(sqlite_backend)
    yield
    _global_db_manager._plugin = original_plugin
