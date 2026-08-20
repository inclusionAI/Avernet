"""Function-scoped pytest fixtures for the endpoint test framework.

``app_with_testing_modules`` builds a **fresh injector per test**
backed by a freshly-created in-memory SQLite database, runs the schema
DDL, and swaps the injector onto ``app.state.injector`` for the
duration of the test. The previous injector is restored in teardown
so legacy tests using the session-level fixtures are unaffected.

``world`` yields a :class:`World` bound to that per-test injector so
case authors can seed and inspect via the same dependency graph the
endpoint handler will resolve.

Both fixtures are exposed via ``tests/endpoints/conftest.py`` so cases
in ``tests/endpoints/`` can request them by name.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector
from httpx import ASGITransport

from tests.community.framework.world import World


# Module-level slot for passing the per-test injector to dependent
# fixtures (``world``) without going through any DI module global.
# The injector is set in ``app_with_testing_modules`` and read by
# ``world`` in the same test scope.
_CURRENT_TEST_INJECTOR: dict[int, Injector] = {}


@pytest.fixture
def app_with_testing_modules(request) -> FastAPI:
    """Yield the FastAPI ``app`` with a per-test injector attached.

    Steps:
      1. Build a fresh ``test``-profile injector via
         ``build_injector(profile=DeployProfile.TEST)``.
         ``TestingDatabaseModule`` calls ``reset_for_tests()`` so the
         underlying engine is brand new.
      2. Bootstrap the schema on the new engine
         (``Base.metadata.create_all``). Required because the engine is
         in-memory — tables don't survive engine disposal.
      3. Save the prior ``app.state.injector``, attach the per-test
         injector via ``attach_injector(app, injector)``, ``yield app``.
      4. On teardown: restore prior ``app.state.injector`` and dispose
         the per-test engine.

    Note: no process-wide injector global. ``attach_injector(app, X)``
    sets ``app.state.injector`` — the only canonical handle. Tests
    that need the injector outside a request reach for it via this
    fixture's return value or the ``world`` fixture.
    """
    # Lazy imports so this module stays import-light: pulling in
    # ``agentclaw.community.adapters.http.app`` triggers the whole router graph + model
    # registration, which we want done exactly once per test session,
    # not at import time of this conftest module.
    from agentclaw.community.adapters.http.app import app
    from agentclaw.community.core.base import Base
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugins.local import database as db_mod

    # B11 (3.2): honor the active deploy profile so this fixture serves both trees —
    # ``test`` (corp-free) for the community runner, ``corp_test`` (corp doubles) for
    # the corp runner. Both are LOCAL-stub + SQLite; ``TestingDatabaseModule`` is in
    # both columns. Reading the profile via ``detect()`` (env-driven) matches the
    # app.py bootstrap and the conftest wiring.
    injector = build_injector(profile=DeployProfile.detect())

    # Force the engine into existence on this injector. ``TestingDatabaseModule``
    # has already called ``reset_for_tests()``, so this is a fresh engine
    # bound to a fresh in-memory database.
    plugin = injector.get(DatabasePlugin)
    with plugin.session() as _s:
        pass
    engine = db_mod._engine
    assert engine is not None, "engine should have been created by SqliteDB session()"

    # Bootstrap the schema. ``adapters/http/app.py`` already imported every
    # ``*models*`` module at app-load time, so ``Base.metadata`` is
    # populated by the time we reach this fixture.
    import agentclaw.community.core.expert_chat.sqlite_models  # noqa: F401

    Base.metadata.create_all(engine)

    # Save the prior ``app.state.injector`` so legacy tests / a bare
    # ``TestClient(app)`` keep seeing the same injector identity after
    # this fixture's teardown.
    prev_app = getattr(app.state, "injector", None)

    attach_injector(app, injector)
    _CURRENT_TEST_INJECTOR[id(request.node)] = injector

    try:
        yield app
    finally:
        _CURRENT_TEST_INJECTOR.pop(id(request.node), None)
        if prev_app is not None:
            attach_injector(app, prev_app)
        # Dispose the per-test engine deterministically; ``reset_for_tests``
        # on the NEXT injector build would do this anyway, but explicit
        # disposal here keeps connection lifecycle observable.
        engine.dispose()


@pytest.fixture
def world(request, app_with_testing_modules) -> World:  # noqa: ARG001 — fixture dep
    """Yield a :class:`World` backed by the same injector ``app`` uses.

    Depending on ``app_with_testing_modules`` ensures the per-test
    injector swap has happened by the time ``world.get(...)`` is called.
    The injector is picked up from the per-test slot the parent
    fixture populates — same identity as ``app.state.injector`` so
    ``Injected(...)`` inside route handlers resolves identically.
    """
    injector = _CURRENT_TEST_INJECTOR[id(request.node)]
    return World(injector)


@pytest_asyncio.fixture
async def async_client(
    app_with_testing_modules: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    """An ``httpx.AsyncClient`` driving the per-test app **in-process** (ASGI, no
    socket) — for endpoint tests that must ``await`` (e.g. to drain a handler's
    fire-and-forget ``asyncio.create_task`` work via
    :func:`tests.community.framework.drain_background_tasks`).

    The sync ``TestClient`` can't reliably run a backgrounded task to completion;
    this can, because the client and the app share the test's event loop. Pairs
    with ``world`` (same per-test injector) — seed/assert via ``world``, drive via
    this client. ``base_url`` is a throwaway placeholder; ``ASGITransport`` never
    opens a socket, so the host is never resolved.
    """
    transport = ASGITransport(app=app_with_testing_modules)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client
