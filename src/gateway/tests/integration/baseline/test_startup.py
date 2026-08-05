from __future__ import annotations

from unittest.mock import patch

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
        routes = {getattr(r, "path", None) for r in client.app.routes}
        assert "/api/test" in routes
        assert "/health" in routes
        assert "/docs" in routes
        assert "/redoc" in routes
        assert "/openapi.json" in routes


class TestApiDocsEnabled:
    def test_swagger_ui_accessible(self, client: TestClient) -> None:
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_redoc_accessible(self, client: TestClient) -> None:
        response = client.get("/redoc")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_openapi_schema_accessible(self, client: TestClient) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data

    def test_openapi_schema_has_metadata(self, client: TestClient) -> None:
        response = client.get("/openapi.json")
        data = response.json()
        assert data["info"]["title"] == "gateway"
        assert data["info"]["description"] != ""
        assert data["info"]["version"] != ""


class TestApiDocsDisabled:
    @pytest.fixture()
    def disabled_client(self) -> TestClient:
        from gateway.community.adapters.web.app import create_app
        from gateway.community.config import ConfigLoader
        from gateway.community.config._models import (
            Config,
            ModuleConfig,
            UserConfig,
            WebConfig,
        )

        disabled_config = Config(
            module_config=ModuleConfig(
                web=WebConfig(enable_api_docs=False),
            ),
            user_config=UserConfig.model_validate(
                {
                    "plugins": {},
                    "upstream_vars": {
                        "backend_server_url": "http://backend:8080",
                        "baas_server_url": "http://baas:9090",
                    },
                    "upstreams": {
                        "base_path": "/openapi/v1",
                        "domains": {
                            "bots": {
                                "server": "backend",
                                "schema": {
                                    "source": "file",
                                    "path": "schemas/bots.openapi.json",
                                },
                            }
                        },
                        "servers": {
                            "backend": {"base_url": "${backend_server_url}"},
                        },
                    },
                    "identity_strategies": {"user": ["google"]},
                    "route_security": {"/**": {"user": "required"}},
                }
            ),
            raw={
                "user_config": {
                    "plugins": {},
                    "upstream_vars": {
                        "backend_server_url": "http://backend:8080",
                        "baas_server_url": "http://baas:9090",
                    },
                    "upstreams": {
                        "base_path": "/openapi/v1",
                        "domains": {
                            "bots": {
                                "server": "backend",
                                "schema": {
                                    "source": "file",
                                    "path": "schemas/bots.openapi.json",
                                },
                            }
                        },
                        "servers": {
                            "backend": {"base_url": "${backend_server_url}"},
                        },
                    },
                    "identity_strategies": {"user": ["google"]},
                    "route_security": {"/**": {"user": "required"}},
                }
            },
        )
        with patch.object(ConfigLoader, "load", return_value=disabled_config):
            app = create_app()
        return TestClient(app)

    def test_swagger_ui_disabled(self, disabled_client: TestClient) -> None:
        response = disabled_client.get("/docs")
        assert response.status_code == 404

    def test_redoc_disabled(self, disabled_client: TestClient) -> None:
        response = disabled_client.get("/redoc")
        assert response.status_code == 404

    def test_openapi_schema_disabled(self, disabled_client: TestClient) -> None:
        response = disabled_client.get("/openapi.json")
        assert response.status_code == 404
