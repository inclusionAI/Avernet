from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "ok"}


class TestHelloEndpoint:
    def test_hello_returns_healthy(self, client: TestClient) -> None:
        response = client.get("/api/test")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["message"] == "hello, i am gw"


class TestAppCreation:
    def test_create_app_returns_non_none(self) -> None:
        from gateway.community.adapters.web.app import create_app

        app = create_app()
        assert app is not None

    def test_app_has_expected_routes(self, client: TestClient) -> None:
        routes = {r.path for r in client.app.routes}
        assert "/api/test" in routes
        assert "/health" in routes
