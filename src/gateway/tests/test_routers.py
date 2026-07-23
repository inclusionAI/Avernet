"""Tests for the public-API router aggregation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.community.adapters.web.app import create_app


def test_openapi_document_served() -> None:
    resp = TestClient(create_app()).get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["openapi"].startswith("3.")


def test_bots_group_is_mounted() -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    assert any(path.startswith("/openapi/v1/bots") for path in paths)
