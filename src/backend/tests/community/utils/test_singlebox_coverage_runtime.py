from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.singlebox_coverage import (
    install_singlebox_coverage_middleware,
)
from agentclaw.community.utils.singlebox_coverage_recorder import (
    record_router_hit,
)


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_recorder_is_noop_when_output_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.delenv("SINGLEBOX_COVERAGE_DIR", raising=False)

    record_router_hit(method="get", route_path="/api/demo", path="/api/demo", status_code=200)

    assert list(tmp_path.iterdir()) == []


def test_recorder_writes_router_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))

    record_router_hit(method="get", route_path="/api/demo/{id}", path="/api/demo/1", status_code=200)

    router_hits = _jsonl(tmp_path / "backend" / "router_hits.jsonl")

    assert router_hits[0]["key"] == "GET /api/demo/{id}"
    assert router_hits[0]["path"] == "/api/demo/1"
    assert router_hits[0]["status_code"] == 200


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


def test_http_middleware_restores_nested_router_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    app = FastAPI()
    install_singlebox_coverage_middleware(app)
    child = APIRouter(prefix="/files")
    parent = APIRouter(prefix="/api/resources")

    @child.get("/{file_id}")
    def get_file(file_id: str):
        return {"file_id": file_id}

    parent.include_router(child)
    app.include_router(parent)

    response = TestClient(app).get("/api/resources/files/report.txt")

    assert response.status_code == 200
    hits = _jsonl(tmp_path / "backend" / "router_hits.jsonl")
    assert hits[0]["key"] == "GET /api/resources/files/{file_id}"
    assert hits[0]["path"] == "/api/resources/files/report.txt"
