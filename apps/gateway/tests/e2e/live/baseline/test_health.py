from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_is_json(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/health")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


class TestHelloEndpoint:
    @pytest.mark.asyncio
    async def test_hello_returns_healthy(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/api/test")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["message"] == "hello, i am gw"

    @pytest.mark.asyncio
    async def test_hello_is_json(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/api/test")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


class TestDIWiredEndpoints:
    @pytest.mark.asyncio
    async def test_openapi_docs_resolve(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/docs")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_route_404(self, http_client: httpx.AsyncClient) -> None:
        response = await http_client.get("/nonexistent/path")
        assert response.status_code == 404
