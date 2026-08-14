"""ASGI TestClient conftest — in-process transport for gateway E2E.

Uses ``fastapi.testclient.TestClient`` backed by ``create_app()`` so tests
exercise the full DI container bootstrap path (PluginContainer Selectors,
initialize_services, auth pipeline) without needing an external server.

The ``app_no_lifespan`` fixture creates a fresh FastAPI app with a noop
lifespan and a clean DI container, then tears it down after each function.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _noop_lifespan(app: FastAPI) -> Any:
    """Replace the real lifespan (with DB startup/refresh) with a noop.

    The real lifespan runs forwarding refresh and DB close — heavy for
    in-process tests that just need to exercise route handlers.
    """

    @asynccontextmanager
    async def _noop(app: FastAPI) -> Generator[None, None, None]:  # noqa: ARG001
        yield

    return _noop


@pytest.fixture(scope="function")
def app_no_lifespan() -> Generator[FastAPI, None, None]:
    """Create a fresh FastAPI app with a noop lifespan and clean DI container.

    Each test function gets a completely isolated app instance with its own
    DI container.  The real lifespan (DB startup, forwarding refresh) is
    replaced with a noop so tests stay fast and don't touch persistent state.

    Old overlay / SERVER_ENV values are restored after the test so stale
    state doesn't leak between test modules.
    """
    old_overlay = os.environ.pop("SOFAPY_CONFIG_OVERLAY", None)
    old_server_env = os.environ.pop("SERVER_ENV", None)
    old_gateway_mode = os.environ.pop("GATEWAY_RUN_MODE", None)
    os.environ["GATEWAY_RUN_MODE"] = "bare"

    from gateway.community.adapters.web.app import create_app

    app = create_app()
    app.router.lifespan_context = _noop_lifespan(app)

    yield app

    # Restore env so stale overlays don't leak.
    if old_overlay is not None:
        os.environ["SOFAPY_CONFIG_OVERLAY"] = old_overlay
    else:
        os.environ.pop("SOFAPY_CONFIG_OVERLAY", None)
    if old_server_env is not None:
        os.environ["SERVER_ENV"] = old_server_env
    else:
        os.environ.pop("SERVER_ENV", None)
    if old_gateway_mode is not None:
        os.environ["GATEWAY_RUN_MODE"] = old_gateway_mode
    else:
        os.environ.pop("GATEWAY_RUN_MODE", None)

    from gateway.community.bootstrap import get_container, shutdown_services

    container = get_container()
    if container is not None:
        shutdown_services(container)


@pytest.fixture(scope="function")
def client(app_no_lifespan: FastAPI) -> Generator[TestClient, None, None]:
    """Synchronous TestClient backed by the isolated app."""
    with TestClient(app_no_lifespan) as c:
        yield c
