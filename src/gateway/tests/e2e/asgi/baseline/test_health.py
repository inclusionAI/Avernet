from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "ok"}

    def test_health_is_json(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


class TestHelloEndpoint:
    def test_hello_returns_healthy(self, client: TestClient) -> None:
        response = client.get("/api/test")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["message"] == "hello, i am gw"

    def test_hello_is_json(self, client: TestClient) -> None:
        response = client.get("/api/test")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


class TestAppCreation:
    def test_create_app_returns_fastapi(self, app_no_lifespan: FastAPI) -> None:
        from fastapi import FastAPI as FA

        assert isinstance(app_no_lifespan, FA)

    def test_routes_registered(self, app_no_lifespan: FastAPI) -> None:
        # `app.routes` also contains `_IncludedRouter` wrappers (from
        # `include_router`) which have no `.path`; only the concrete route
        # objects do.
        route_paths = {
            route.path for route in app_no_lifespan.routes if hasattr(route, "path")
        }
        for p in ("/health", "/api/test"):
            assert p in route_paths, f"Expected route {p} not found in {route_paths}"
