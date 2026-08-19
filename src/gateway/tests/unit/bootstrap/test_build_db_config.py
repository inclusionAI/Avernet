"""Unit tests for the DI bootstrap database config builder.

Mirrors the production layout
``src/gateway/community/bootstrap/_container.py`` and exercises
``_build_db_config`` across its structured and fallback branches so the
changed lines added with the MariaDB plugin are covered without a live
database.
"""

from __future__ import annotations

import pytest
from dependency_injector import providers

from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.bootstrap._container import _build_db_config
from gateway.community.spi.database import PluginDatabaseType


def _config(**database) -> providers.Configuration:
    cfg = providers.Configuration()
    data: dict = {
        "plugins": {
            "database": {
                "plugin_database": PluginDatabaseType.MARIADB_ORM.value,
                "database_url": "mysql+aiomysql://u:p@host:3306/db",
                "create_schema": "true",
                "seed_data": "off",
                "mariadb_host": "db.internal",
                "mariadb_port": "3307",
                "mariadb_database": "mydb",
                "mariadb_user": "user",
                "mariadb_password": "secret",
            }
        }
    }
    data["plugins"]["database"].update(database)
    cfg.from_dict(data)
    return cfg


class TestBuildDbConfigMariaDb:
    def test_reads_structured_mariadb_settings(self) -> None:
        out = _build_db_config(_config())
        assert isinstance(out, DatabaseConfig)
        assert out.plugin_type == PluginDatabaseType.MARIADB_ORM.value
        assert out.db_url == "mysql+aiomysql://u:p@host:3306/db"
        assert out.mariadb_host == "db.internal"
        assert out.mariadb_port == 3307
        assert out.mariadb_database == "mydb"
        assert out.mariadb_user == "user"
        assert out.mariadb_password == "secret"
        assert out.create_schema is True
        assert out.seed_data is False

    def test_missing_db_url_defaults_to_empty(self) -> None:
        out = _build_db_config(_config(database_url=None))
        assert out.db_url == ""
        assert isinstance(out, DatabaseConfig)

    def test_missing_bool_defaults_to_false(self) -> None:
        out = _build_db_config(_config(create_schema=None, seed_data=None))
        assert out.create_schema is False
        assert out.seed_data is False

    def test_missing_optional_fields_fall_back_to_defaults(self) -> None:
        out = _build_db_config(_config(mariadb_host=None, mariadb_port=None))
        assert out.mariadb_host == "127.0.0.1"
        assert out.mariadb_port == 3306

    def test_bool_strings_are_coerced(self) -> None:
        out = _build_db_config(_config(create_schema="yes", seed_data="off"))
        assert out.create_schema is True
        assert out.seed_data is False

    def test_sqlite_requires_database_url(self) -> None:
        cfg = _config(
            plugin_database=PluginDatabaseType.SQLITE_ORM.value, database_url=None
        )
        with pytest.raises(Exception):
            _build_db_config(cfg)
