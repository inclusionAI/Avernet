"""Tests for the public-API router aggregator and app wiring."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from gateway.community.adapters.web import routers
from gateway.community.adapters.web.app import create_app


def test_openapi_document_served() -> None:
    client = TestClient(create_app())
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["openapi"].startswith("3.")


def test_include_all_mounts_registered_routers() -> None:
    router = APIRouter()

    @router.get("/openapi/v1/_probe")
    async def _probe() -> dict[str, str]:
        return {"ok": "true"}

    app = FastAPI()
    original = list(routers.GROUP_ROUTERS)
    routers.GROUP_ROUTERS.append(router)
    try:
        routers.include_all(app)
        paths = TestClient(app).get("/openapi.json").json()["paths"]
        assert "/openapi/v1/_probe" in paths
    finally:
        routers.GROUP_ROUTERS[:] = original


def test_include_all_is_noop_without_registered_routers() -> None:
    app = FastAPI()
    before = len(app.routes)
    original = list(routers.GROUP_ROUTERS)
    routers.GROUP_ROUTERS.clear()
    try:
        routers.include_all(app)
        assert len(app.routes) == before
    finally:
        routers.GROUP_ROUTERS[:] = original
