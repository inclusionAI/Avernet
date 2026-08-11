"""Bootstrap DI container fixture for integration tests."""

from __future__ import annotations

import asyncio
import os

import pytest

from secbaas.community.bootstrap import (
    ApplicationContainer,
    get_container,
    initialize_services,
    load_container_config,
    set_container,
    shutdown_services,
)
from secbaas.community.core.utils.env_utils import get_current_env

# Shared test constants
TEST_ENV = get_current_env()
"""Environment name used consistently across all integration tests."""


@pytest.fixture(scope="session")
def bootstrap_init() -> ApplicationContainer:
    old_overlay = os.environ.pop("SOFAPY_CONFIG_OVERLAY", None)
    # Allow running the acceptance/E2E suite against a different backend
    # (e.g. `TEST_OVERLAY=mariadb` for the MariaDB storage backend). Defaults
    # to the in-memory SQLite overlay so unit/contract tests stay hermetic.
    os.environ["SOFAPY_CONFIG_OVERLAY"] = os.environ.get("TEST_OVERLAY", "it-sqlite")

    try:
        user_config = load_container_config()
        container = get_container()
        container.config.from_dict(user_config)

        asyncio.run(initialize_services(container))

        set_container(container)
        bootstrap_init._container = container
        # Wire the bootstrap container into the test app and replace lifespan
        # with a noop so TestClient doesn't create a competing container.
        from contextlib import asynccontextmanager

        from secbaas.community.adapters.web.app import app  # noqa: E402

        app.container = container

        @asynccontextmanager
        async def _noop_lifespan(app):  # noqa: ARG001
            yield

        app.router.lifespan_context = _noop_lifespan
        yield container
    finally:
        try:
            asyncio.run(shutdown_services(container))
        except BaseException as exc:
            print(f"[bootstrap] shutdown_services error (ignored): {exc}")

        if old_overlay is not None:
            os.environ["SOFAPY_CONFIG_OVERLAY"] = old_overlay
        else:
            os.environ.pop("SOFAPY_CONFIG_OVERLAY", None)


@pytest.fixture(scope="session")
def db_manager(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.db_manager()


@pytest.fixture
def db_transaction():
    from unittest import mock

    return mock.MagicMock()


@pytest.fixture(scope="session")
def skip_if_zdas_unavailable():
    return None


@pytest.fixture(scope="session")
def bot_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.bot_repository()


@pytest.fixture(scope="session")
def device_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.device_repository()


@pytest.fixture(scope="session")
def rel_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.bot_device_rel_repository()


@pytest.fixture(scope="session")
def session_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.bot_session_repository()


@pytest.fixture(scope="session")
def tenant_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.tenant_repository()


@pytest.fixture(scope="session")
def system_config_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.system_config_repository()


@pytest.fixture(scope="session")
def publish_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.publish_repository()


@pytest.fixture(scope="session")
def publish_batch_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.publish_batch_repository()


@pytest.fixture(scope="session")
def publish_record_repository(bootstrap_init: ApplicationContainer):
    return bootstrap_init.repository.publish_record_repository()
