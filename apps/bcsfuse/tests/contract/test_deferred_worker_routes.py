"""Online state writes finish before deferred vector work."""

import asyncio
import json

from fastapi import FastAPI
from src.bootstrap import oss_worker_lifecycle_routes as lifecycle


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


def test_online_returns_before_vector_sync(monkeypatch):
    events = []

    class RuntimeService:
        def set_online(self, worker_id, *, updated_by, defer_vector_sync):
            assert defer_vector_sync is True
            events.append("state_written")
            return worker_id

        def sync_online_vectors_if_current(self, worker_id):
            events.append("vector_synced")

    monkeypatch.setattr(lifecycle, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(lifecycle, "_get_runtime_service", lambda request: RuntimeService())
    monkeypatch.setattr(lifecycle, "_runtime_response", lambda worker: {"worker_id": worker})
    app = FastAPI()
    app.include_router(lifecycle.router)

    async def invoke():
        body_events = await _invoke(app, "PUT", "/v1/workers/bot:owner/online", timeline=events)
        return body_events

    body_events = asyncio.run(invoke())
    assert body_events[0] == ("status", 200)
    assert body_events[1] == ("response", {"worker_id": "bot:owner"})
    assert events == ["state_written", "response", "vector_synced"]
