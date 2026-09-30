"""Tests for BaaS singlebox coverage adapter hooks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from secbaas.community.adapters.web import singlebox_coverage


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_append_jsonl_ignores_when_coverage_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("SINGLEBOX_COVERAGE", raising=False)
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))

    singlebox_coverage._append_jsonl("router_hits.jsonl", {"key": "GET /health"})

    assert not (tmp_path / "baas" / "router_hits.jsonl").exists()


def test_append_jsonl_ignores_when_coverage_dir_missing(monkeypatch) -> None:
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.delenv("SINGLEBOX_COVERAGE_DIR", raising=False)

    singlebox_coverage._append_jsonl("router_hits.jsonl", {"key": "GET /health"})

    assert singlebox_coverage._coverage_dir() is None


def test_append_jsonl_writes_timestamped_event(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))

    singlebox_coverage._append_jsonl(
        "router_hits.jsonl",
        {"key": "GET /health", "status_code": 200},
    )

    events = _read_jsonl(tmp_path / "baas" / "router_hits.jsonl")
    assert len(events) == 1
    assert events[0]["key"] == "GET /health"
    assert events[0]["status_code"] == 200
    assert isinstance(events[0]["ts"], str)


def test_middleware_records_route_template_and_response_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    singlebox_coverage.install_singlebox_coverage_middleware(app)

    with TestClient(app) as client:
        response = client.get("/items/abc")

    assert response.status_code == 200
    events = _read_jsonl(tmp_path / "baas" / "router_hits.jsonl")
    assert events[-1] == {
        "key": "GET /items/{item_id}",
        "method": "GET",
        "path": "/items/abc",
        "route_path": "/items/{item_id}",
        "status_code": 200,
        "ts": events[-1]["ts"],
    }


def test_middleware_falls_back_to_request_path_for_unmatched_route(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setenv("SINGLEBOX_COVERAGE_DIR", str(tmp_path))
    app = FastAPI()
    singlebox_coverage.install_singlebox_coverage_middleware(app)

    with TestClient(app) as client:
        response = client.post("/missing")

    assert response.status_code == 404
    events = _read_jsonl(tmp_path / "baas" / "router_hits.jsonl")
    assert events[-1]["key"] == "POST /missing"
    assert events[-1]["method"] == "POST"
    assert events[-1]["path"] == "/missing"
    assert events[-1]["route_path"] == "/missing"
    assert events[-1]["status_code"] == 404


def test_create_app_installs_singlebox_coverage_middleware(monkeypatch) -> None:
    from secbaas.community.adapters.web import app as app_module

    install = MagicMock()
    monkeypatch.setenv("SINGLEBOX_COVERAGE", "1")
    monkeypatch.setattr(
        singlebox_coverage,
        "install_singlebox_coverage_middleware",
        install,
    )

    created_app = app_module.create_app()

    install.assert_called_once_with(created_app)
