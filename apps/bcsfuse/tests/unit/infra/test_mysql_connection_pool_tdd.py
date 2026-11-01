"""Fail-fast contracts for the MySQL connection pool."""

from __future__ import annotations

import pytest


def test_mysql_connection_pool_uses_a_bounded_connection_timeout(
    monkeypatch,
) -> None:
    from app.infra.public.database import mysql_connection_pool as module

    captured: dict[str, object] = {}

    class Factory:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def get_connection(self) -> object:
            return object()

    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", Factory)

    provider = module.MySQLConnectionPoolProvider(
        host="127.0.0.1",
        port=3306,
        user="contract",
        password="not-used",
        database="bcsfuse",
        pool_size=1,
    )
    provider.get_connection()

    assert captured["connection_timeout"] == 3.0


def test_mysql_connection_pool_timeout_is_configurable(monkeypatch) -> None:
    from app.infra.public.database import mysql_connection_pool as module

    captured: dict[str, object] = {}

    class Factory:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def get_connection(self) -> object:
            return object()

    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", Factory)
    monkeypatch.setenv("MYSQL_CONNECT_TIMEOUT", "7")

    module.MySQLConnectionPoolProvider().get_connection()

    assert captured["connection_timeout"] == 7.0


def test_mysql_connection_pool_timeout_is_safe_for_c_extensions(
    monkeypatch,
) -> None:
    from app.infra.public.database import mysql_connection_pool as module

    captured: dict[str, object] = {}

    class Factory:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def get_connection(self) -> object:
            return object()

    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", Factory)

    provider = module.MySQLConnectionPoolProvider(connection_timeout=0.5)
    provider.get_connection()

    assert captured["connection_timeout"] == 1


def test_mysql_connection_pool_timeout_rejects_invalid_values() -> None:
    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    with pytest.raises(ValueError, match="connection_timeout must be positive"):
        MySQLConnectionPoolProvider(connection_timeout=0)


def test_mysql_connection_pool_diagnostics_expose_timeout_configuration() -> None:
    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    provider = MySQLConnectionPoolProvider(
        host="127.0.0.1",
        port=3306,
        user="contract",
        password="not-used",
        database="bcsfuse",
        pool_size=1,
        connection_timeout=7,
    )

    assert provider.get_diagnostics()["connection_timeout"] == 7.0


def test_mysql_pool_default_is_scale_safe(monkeypatch) -> None:
    import os

    monkeypatch.delenv(os.environ.get("MYSQL_POOL_SIZE", ""), raising=False)
    monkeypatch.delenv("MYSQL_POOL_SIZE", raising=False)

    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    provider = MySQLConnectionPoolProvider()

    assert provider.pool_size == 8


def test_mysql_pool_size_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_POOL_SIZE", "4")

    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    provider = MySQLConnectionPoolProvider()

    assert provider.pool_size == 4


def test_mysql_pool_rejects_invalid_and_unscaleable_sizes(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_POOL_SIZE", "0")

    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    with pytest.raises(ValueError, match="pool_size must be"):
        MySQLConnectionPoolProvider()

    monkeypatch.setenv("MYSQL_POOL_SIZE", "65")
    with pytest.raises(ValueError, match="pool_size must not exceed"):
        MySQLConnectionPoolProvider()


def test_runtime_mysql_pool_composition_uses_configuration(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "contract")
    monkeypatch.setenv("MYSQL_PASSWORD", "not-used")
    monkeypatch.setenv("MYSQL_DATABASE", "bcsfuse")
    monkeypatch.setenv("MYSQL_POOL_SIZE", "4")

    from app.bootstrap.opensource import create_runtime_mysql_pool

    provider = create_runtime_mysql_pool()

    assert provider.pool_size == 4


def test_mysql_pool_diagnostics_expose_scale_configuration(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_POOL_SIZE", "4")

    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    provider = MySQLConnectionPoolProvider()

    assert provider.get_diagnostics()["pool_size"] == 4


def test_mysql_pool_retries_transient_connection_quota_exhaustion(
    monkeypatch,
) -> None:
    from mysql.connector import Error

    from app.infra.public.database import mysql_connection_pool as module

    calls: list[int] = []
    sleeps: list[float] = []

    class Factory:
        def __init__(self, **kwargs: object) -> None:
            calls.append(1)

        def get_connection(self) -> object:
            return object()

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("MYSQL_STARTUP_RETRY_INTERVAL_MS", "10")
    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", Factory)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    original_factory = Factory

    class RetryFactory(Factory):
        def __init__(self, **kwargs: object) -> None:
            if len(calls) < 2:
                calls.append(1)
                raise Error(errno=1040, msg="connection quota exhausted")
            original_factory(**kwargs)

    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", RetryFactory)

    provider = module.MySQLConnectionPoolProvider(pool_size=1)
    provider.get_connection()

    assert len(calls) == 3
    assert sleeps == [0.01, 0.02]


def test_mysql_pool_does_not_retry_non_quota_errors(monkeypatch) -> None:
    from mysql.connector import Error

    from app.infra.public.database import mysql_connection_pool as module

    calls: list[int] = []
    sleeps: list[float] = []

    class Factory:
        def __init__(self, **kwargs: object) -> None:
            calls.append(1)
            raise Error(errno=2003, msg="connection refused")

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("MYSQL_STARTUP_RETRY_INTERVAL_MS", "10")
    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", Factory)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    provider = module.MySQLConnectionPoolProvider(pool_size=1)

    with pytest.raises(RuntimeError, match="Failed to initialize MySQL connection pool"):
        provider.get_connection()

    assert len(calls) == 1
    assert sleeps == []


def test_mysql_pool_retry_configuration_is_fail_fast(monkeypatch) -> None:
    from app.infra.public.database import mysql_connection_pool as module

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "0")
    with pytest.raises(ValueError, match="MYSQL_STARTUP_RETRY_ATTEMPTS"):
        module.mysql_startup_retry_attempts()

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "31")
    with pytest.raises(ValueError, match="MYSQL_STARTUP_RETRY_ATTEMPTS"):
        module.mysql_startup_retry_attempts()

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("MYSQL_STARTUP_RETRY_INTERVAL_MS", "0")
    with pytest.raises(ValueError, match="MYSQL_STARTUP_RETRY_INTERVAL_MS"):
        module.mysql_startup_retry_delay_ms()


def test_mysql_pool_failure_cooldown_prevents_startup_retry_storm(
    monkeypatch,
) -> None:
    from mysql.connector import Error

    from app.infra.public.database import mysql_connection_pool as module

    calls: list[int] = []

    class ExhaustedFactory:
        def __init__(self, **kwargs: object) -> None:
            calls.append(1)
            raise Error(errno=1040, msg="connection quota exhausted")

    monkeypatch.setenv("MYSQL_STARTUP_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("MYSQL_STARTUP_RETRY_INTERVAL_MS", "1")
    monkeypatch.setattr(module.pooling, "MySQLConnectionPool", ExhaustedFactory)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    provider = module.MySQLConnectionPoolProvider(
        host="127.0.0.1",
        port=3306,
        user="contract",
        password="not-used",
        database="bcsfuse",
        pool_size=1,
    )

    with pytest.raises(RuntimeError, match="Failed to initialize MySQL"):
        provider.get_connection()
    assert len(calls) == 2

    with pytest.raises(RuntimeError, match="MySQL connection pool is in cooldown"):
        provider.get_connection()
    assert len(calls) == 2

    # After the bounded cooldown expires, a rolling pod may retry rather than
    # remaining permanently unavailable until manual restart.
    provider._pool_failure_cooldown_seconds = 0.0
    with pytest.raises(RuntimeError, match="Failed to initialize MySQL"):
        provider.get_connection()
    assert len(calls) == 4


def test_mysql_pool_failure_cooldown_default_is_scale_safe() -> None:
    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    provider = MySQLConnectionPoolProvider(
        host="127.0.0.1",
        port=3306,
        user="contract",
        password="not-used",
        database="bcsfuse",
        pool_size=1,
    )

    assert provider._pool_failure_cooldown_seconds == 30.0
