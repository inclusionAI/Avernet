from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.singlebox_coverage import (
    install_singlebox_coverage_middleware,
)
from agentclaw.community.utils.singlebox_coverage_proxy import wrap_for_singlebox_coverage
from agentclaw.community.utils.singlebox_coverage_recorder import (
    record_plugin_hit,
    record_router_hit,
)


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_recorder_is_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("SINGLEBOX_COVERAGE", raising=False)
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))

    record_plugin_hit("Plugin call")

    assert not (tmp_path / "backend").exists()


def test_recorder_is_noop_when_output_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.delenv("SINGLEBOX_COVERAGE_DIR", raising=False)

    record_router_hit(method="get", route_path="/api/demo", path="/api/demo", status_code=200)

    assert list(tmp_path.iterdir()) == []


def test_recorder_writes_router_and_plugin_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))

    record_router_hit(method="get", route_path="/api/demo/{id}", path="/api/demo/1", status_code=200)
    record_plugin_hit("DemoPlugin call", resource="demo")

    router_hits = _jsonl(tmp_path / "backend" / "router_hits.jsonl")
    plugin_hits = _jsonl(tmp_path / "backend" / "plugin_hits.jsonl")

    assert router_hits[0]["key"] == "GET /api/demo/{id}"
    assert router_hits[0]["path"] == "/api/demo/1"
    assert router_hits[0]["status_code"] == 200
    assert plugin_hits[0]["key"] == "DemoPlugin call"
    assert plugin_hits[0]["resource"] == "demo"


def test_proxy_returns_target_when_disabled(monkeypatch):
    class Target:
        def ping(self):
            return "pong"

    target = Target()
    monkeypatch.delenv("SINGLEBOX_COVERAGE", raising=False)

    assert wrap_for_singlebox_coverage(target, {"ping": "Target ping"}) is target


def test_proxy_records_sync_methods_and_preserves_plain_attrs(monkeypatch, tmp_path):
    class Target:
        value = "plain"

        def ping(self, name: str):
            return f"pong:{name}"

    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    proxy = wrap_for_singlebox_coverage(
        Target(),
        {"ping": "Target ping"},
        attrs=lambda method, args, kwargs, result: {
            "method_name": method,
            "arg": args[0],
            "result": result,
        },
    )

    assert proxy.value == "plain"
    assert proxy.ping("demo") == "pong:demo"

    hits = _jsonl(tmp_path / "backend" / "plugin_hits.jsonl")
    assert hits[0]["key"] == "Target ping"
    assert hits[0]["method"] == "ping"
    assert hits[0]["method_name"] == "ping"
    assert hits[0]["arg"] == "demo"
    assert hits[0]["result"] == "pong:demo"


@pytest.mark.asyncio
async def test_proxy_records_async_methods(monkeypatch, tmp_path):
    class Target:
        async def ping(self):
            return "pong"

    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    proxy = wrap_for_singlebox_coverage(Target(), {"ping": "Target ping"})

    assert await proxy.ping() == "pong"

    hits = _jsonl(tmp_path / "backend" / "plugin_hits.jsonl")
    assert hits[0]["key"] == "Target ping"
    assert hits[0]["method"] == "ping"


def test_http_middleware_records_fastapi_route_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    app = FastAPI()
    install_singlebox_coverage_middleware(app)

    @app.get("/api/demo/{item_id}")
    def get_demo(item_id: str):
        return {"item_id": item_id}

    response = TestClient(app).get("/api/demo/42")

    assert response.status_code == 200
    hits = _jsonl(tmp_path / "backend" / "router_hits.jsonl")
    assert hits[0]["key"] == "GET /api/demo/{item_id}"
    assert hits[0]["path"] == "/api/demo/42"
