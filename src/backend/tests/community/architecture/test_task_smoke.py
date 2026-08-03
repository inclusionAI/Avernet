"""End-to-end smoke for the task module (Phase 0.10 → updated Phase 1/2).

Validates the full wiring chain against a REAL SQLite-backed injector:
- the injector resolves the router's ``Injected(TaskService)`` dep to the REAL
  TaskService (Phase 2 swapped the Phase-0 Noop override, plan §2.5) backed by
  the ORM repos (Phase 1, ``ac_task`` / ``ac_task_event``).
- the FastAPI ``app`` mounts the task router (routes present).
- POST /api/tasks/create returns 200 with the real drafting Task payload (not 501) and
  actually persists (Phase 1.6: events land in the log).
- get/progress on an unknown id return 404 / {} — proves the handler ran against
  the real binding (a 501 would mean unwired DI).

Uses the TEST profile (TestingDatabaseModule → in-memory SQLite with
``reset_for_tests()``) so the smoke runs isolated without a file DB.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.core.task.protocols import TaskService
from agentclaw.community.core.task.services import TaskService as RealTaskService
from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.plugins.community.task import NoopTaskService


def _force_fresh_in_memory_engine(injector: Injector) -> None:
    """Force a brand-new in-memory SQLite engine on the injector and build the
    schema. Mirrors the framework fixture (force ``session()`` → ``create_all``)
    so the ORM-backed repos have tables to write to."""
    from agentclaw.community.core.base import Base
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.plugins.local import database as db_mod
    # Register the task ORM models on Base.metadata before create_all (the test
    # path doesn't run the async LocalDatabasePlugin.bootstrap() that normally
    # does this side-effect import).
    import agentclaw.community.core.task.repository.models  # noqa: F401

    plugin = injector.get(DatabasePlugin)
    with plugin.session() as _s:  # noqa: F841 — force engine creation
        pass
    engine = db_mod._engine
    assert engine is not None
    Base.metadata.create_all(engine)


def _build_injector() -> Injector:
    # TEST profile = TestingDatabaseModule (in-memory SQLite) + CommunityTaskModule
    # (real TaskService + ORM repos) via the shared community column.
    return build_injector(profile=DeployProfile.TEST)


def test_injector_resolves_task_service_as_real():
    """Phase 2 (plan §2.5): TaskService binds to the real impl, not Noop."""
    inj = _build_injector()
    svc = inj.get(TaskService)
    assert isinstance(svc, RealTaskService)
    assert not isinstance(svc, NoopTaskService)


@pytest.fixture
def client():
    """Yield a TestClient bound to a per-test TEST-profile injector with a fresh
    in-memory SQLite schema."""
    from agentclaw.community.adapters.http.app import app

    inj = _build_injector()
    _force_fresh_in_memory_engine(inj)
    prev = getattr(app.state, "injector", None)
    attach_injector(app, inj)
    try:
        yield TestClient(app)
    finally:
        if prev is not None:
            attach_injector(app, prev)
        from agentclaw.community.plugins.local.database import _engine
        if _engine is not None:
            _engine.dispose()


def _client() -> TestClient:  # legacy generator retained for the older tests below
    from agentclaw.community.adapters.http.app import app

    inj = _build_injector()
    _force_fresh_in_memory_engine(inj)
    prev = getattr(app.state, "injector", None)
    attach_injector(app, inj)
    try:
        yield TestClient(app)
    finally:
        if prev is not None:
            attach_injector(app, prev)


def test_app_mounts_task_router():
    from agentclaw.community.adapters.http.app import app
    spec = app.openapi()
    paths = set(spec.get("paths", {}).keys())
    assert "/api/tasks" in paths
    assert "/api/tasks/{task_id}" in paths
    assert "/api/tasks/{task_id}/events" in paths


def test_smoke_create_task_200_with_noop():
    client_gen = _client()
    client = next(client_gen)
    try:
        r = client.post("/api/tasks/create", json={"title": "smoke", "source": "api"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "drafting"
        assert body["task_id"]  # NoopTaskService.create returns a Task with an id
    finally:
        try:
            next(client_gen)
        except StopIteration:
            pass


def test_smoke_noop_get_returns_404_not_501():
    # NoopTaskService.get -> None -> router raises 404. Proves the handler ran
    # against the Noop binding (a 501 would mean unwired DI).
    client_gen = _client()
    client = next(client_gen)
    try:
        r = client.get("/api/tasks/nope")
        assert r.status_code == 404
    finally:
        try:
            next(client_gen)
        except StopIteration:
            pass


def test_smoke_noop_progress_returns_200_with_empty():
    # NoopTaskService.progress -> {} (a dict), router returns 200 with defaults.
    # Proves the handler ran against the Noop binding (a 501 would mean unwired).
    client_gen = _client()
    client = next(client_gen)
    try:
        r = client.get("/api/tasks/nope/progress")
        assert r.status_code == 200
        body = r.json()
        assert body["loop_round"] == 0
        assert body["done"] == 0
    finally:
        try:
            next(client_gen)
        except StopIteration:
            pass


def test_smoke_list_returns_empty_200():
    client_gen = _client()
    client = next(client_gen)
    try:
        r = client.get("/api/tasks?user_id=u1")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
    finally:
        try:
            next(client_gen)
        except StopIteration:
            pass


def test_smoke_create_persists_and_events_land_in_log(client):
    """Phase 1.6 e2e: create persists the task snapshot; an owner-bot 回投 event
    folded via ``POST /events`` lands in the event log (single-writer seq)."""
    from agentclaw.community.core.task.domain.repository import TaskEventRepo

    # create
    r = client.post("/api/tasks/create", json={"title": "persist-e2e", "source": "api"})
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    inj = client.app.state.injector
    event_repo = inj.get(TaskEventRepo)
    # TASK_CREATED + (no clarify yet) → 1 event in the log (create emits one)
    assert event_repo.latest_seq(task_id) == 1

    # POST an owner-bot 回投 event (kind/payload envelope)
    evt = client.post(
        f"/api/tasks/{task_id}/events",
        json={"kind": "task.clarified", "seq": 0, "payload": {"patch": {"summary": "s"}}},
    )
    assert evt.status_code == 200
    assert evt.json()["accepted"] is True
    # the external event was appended → seq advanced to 2
    assert event_repo.latest_seq(task_id) == 2
    events = event_repo.load_events(task_id)
    assert [e.seq for e in events] == [1, 2]
    assert events[0].kind.value == "task.created"
    assert events[1].kind.value == "task.clarified"