"""Worker route responses preserve their vector and analysis ordering."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from src.bootstrap import oss_worker_lifecycle_routes as lifecycle
from src.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from src.infra.adapters.in_memory_worker_runtime_state_store import (
    InMemoryWorkerRuntimeStateStore,
)
from src.interfaces.api import profile_routes, worker_profile_parity_routes
from src.interfaces.api.dependencies import fusion_dependencies
from src.interfaces.api.worker_profile_routes import analysis, common, vector_sync


async def _invoke(app, method, path, payload=None, timeline=None):
    events = []
    body = json.dumps(payload).encode() if payload is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 12345), "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            events.append(("status", message["status"]))
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            events.append(("response", json.loads(message["body"])))
            if timeline is not None:
                timeline.append("response")

    await app(scope, receive, send)
    return events


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
def test_online_waits_for_vector_sync_off_event_loop(monkeypatch, prefix):
    events = []
    service_threads = []
    request_thread = threading.get_ident()

    class RuntimeService:
        def set_online(self, worker_id, *, updated_by, defer_vector_sync=False):
            service_threads.append(threading.get_ident())
            events.append("state_written")
            if not defer_vector_sync:
                events.append("vector_synced")
            return worker_id

        def sync_online_vectors_if_current(self, worker_id):
            events.append("vector_synced")

    monkeypatch.setattr(lifecycle, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(lifecycle, "_get_runtime_service", lambda request: RuntimeService())
    monkeypatch.setattr(lifecycle, "_runtime_response", lambda worker: {"worker_id": worker})
    app = FastAPI()
    app.include_router(lifecycle.router)
    app.include_router(lifecycle.external_router)

    async def invoke():
        body_events = await _invoke(app, "PUT", f"{prefix}/workers/bot:owner/online", timeline=events)
        return body_events

    body_events = asyncio.run(invoke())
    assert body_events[0] == ("status", 200)
    assert body_events[1] == ("response", {"worker_id": "bot:owner"})
    assert events == ["state_written", "vector_synced", "response"]
    assert service_threads[0] != request_thread


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
def test_online_vector_write_failure_returns_existing_error(monkeypatch, prefix):
    class RuntimeService:
        def set_online(self, worker_id, *, updated_by):
            raise RuntimeError("vector write failed")

    monkeypatch.setattr(lifecycle, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(lifecycle, "_get_runtime_service", lambda request: RuntimeService())
    app = FastAPI()
    app.include_router(lifecycle.router)
    app.include_router(lifecycle.external_router)
    events = asyncio.run(_invoke(app, "PUT", f"{prefix}/workers/bot:owner/online"))
    assert events == [
        ("status", 500),
        ("response", {"detail": {
            "code": "RUNTIME_STATE_UPDATE_ERROR",
            "message": "Failed to persist worker runtime state",
        }}),
    ]


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("sync_llm", [False, True])
def test_sync_waits_for_index_and_optional_analysis_without_blocking_loop(
    monkeypatch, prefix, sync_llm,
):
    timeline = []
    index_threads = []
    vector_threads = []
    app = FastAPI()
    app.state.context = SimpleNamespace(registry={
        "worker_registry_store": InMemoryWorkerRegistryStore(),
        "worker_runtime_state_store": InMemoryWorkerRuntimeStateStore(),
        "worker_profile_content_store": MagicMock(),
    })
    app.include_router(worker_profile_parity_routes.api_router, prefix="/api/v1")
    app.include_router(worker_profile_parity_routes.compat_router, prefix="/v1")
    monkeypatch.setattr(common, "_require_auth", lambda request: None)

    def sync_vector(*args):
        vector_threads.append(threading.get_ident())

    monkeypatch.setattr(vector_sync, "_sync_runtime_state_to_vector_store", sync_vector)
    monkeypatch.setattr(vector_sync, "_sync_availability_to_vector_store", sync_vector)
    monkeypatch.setattr(profile_routes, "_trigger_index_sync", lambda worker_id: None)
    monkeypatch.setattr(
        "src.application.utils.drm_config_helper.is_capability_verify_enabled",
        lambda: False,
    )

    def build_index(worker_id):
        assert worker_id == "bot:owner"
        index_threads.append(threading.get_ident())
        timeline.append("index_done")
        return True

    monkeypatch.setattr(fusion_dependencies, "_build_vector_index_for_worker", build_index)

    async def run():
        request_thread = threading.get_ident()
        analysis_started = asyncio.Event()
        allow_analysis_to_finish = asyncio.Event()
        analysis_done = asyncio.Event()

        async def analyze(**kwargs):
            timeline.append("analysis_started")
            analysis_started.set()
            await allow_analysis_to_finish.wait()
            timeline.append("analysis_done")
            analysis_done.set()

        monkeypatch.setattr(analysis, "_analyze_and_persist_async", analyze)
        request_task = asyncio.create_task(_invoke(
            app, "POST", f"{prefix}/workers/bot:owner/sync",
            {"name": "Bot", "sync_llm": sync_llm, "profile": {"profile_id": "default"}},
            timeline=timeline,
        ))
        await asyncio.wait_for(analysis_started.wait(), timeout=3)
        assert timeline[0] == "index_done"
        if sync_llm:
            assert "response" not in timeline
        else:
            await asyncio.wait_for(request_task, timeout=3)
            assert "response" in timeline
            assert "analysis_done" not in timeline

        allow_analysis_to_finish.set()
        response_events = await asyncio.wait_for(request_task, timeout=3)
        await asyncio.wait_for(analysis_done.wait(), timeout=3)
        assert response_events[0] == ("status", 200)
        if sync_llm:
            assert timeline.index("analysis_done") < timeline.index("response")
        else:
            assert timeline.index("response") < timeline.index("analysis_done")
        return request_thread

    request_thread = asyncio.run(run())
    assert len(vector_threads) == 2
    assert all(thread != request_thread for thread in vector_threads)
    assert len(index_threads) == 1
    assert index_threads[0] != request_thread
