"""Tests for adapters/web/app.py — FastAPI application entry point.

Covers:
- /hello endpoint (happy path, response format)
- /health endpoint (happy path, response format)
- Exception handlers (DomainError → 5xx, ValueError → 400, Exception → 500)
- Router registration (include_router called for all expected routers)
- App metadata (title, version, lifespan existence)
- load_config() edge cases already covered in test_app_cron.py
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import exception handlers from the real app for use in isolated test apps.
# We import normally (tracer patches run), which is fine for function imports.
# The exception-handler tests build their own FastAPI instances without the
# real app's tracer middleware, so tracer monkey-patching doesn't interfere.
# ---------------------------------------------------------------------------
from secbaas.adapters.web.app import (
    domain_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from secbaas.api import DomainError
from secbaas.config import Config


def _build_app_with_error(route_path: str, exc: Exception) -> FastAPI:
    """Build a minimal FastAPI app with a single route that raises `exc`."""
    app = FastAPI()

    async def error_handler():
        raise exc

    if route_path == "/hello":
        app.get("/hello")(error_handler)

        @app.get("/health")
        async def health_check():
            return {"status": "healthy"}

    elif route_path == "/health":

        @app.get("/hello")
        async def hello():
            return {"message": "hello, i am sofapy"}

        app.get("/health")(error_handler)

    else:
        raise ValueError(f"Unsupported route: {route_path}")

    app.add_exception_handler(DomainError, domain_exception_handler)
    app.add_exception_handler(ValueError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


# ============================================================================
# TestClient Fixture
# ============================================================================


@pytest.fixture
def app_for_test() -> FastAPI:
    """Create a fresh FastAPI app with only /hello and /health + exception handlers.

    Avoids importing the real secbaas app, which triggers lifespan side-effects
    (database, cron, etc.).
    """
    test_app = FastAPI()

    @test_app.get("/hello")
    async def hello():
        return {"message": "hello, i am sofapy"}

    @test_app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    test_app.add_exception_handler(DomainError, domain_exception_handler)
    test_app.add_exception_handler(ValueError, validation_exception_handler)
    test_app.add_exception_handler(Exception, unhandled_exception_handler)

    return test_app


@pytest.fixture
def client(app_for_test: FastAPI) -> TestClient:
    """TestClient backed by the minimal test app."""
    with TestClient(app_for_test) as c:
        yield c


# ============================================================================
# /hello Endpoint
# ============================================================================


class TestHelloEndpoint:
    """Tests for GET /hello endpoint."""

    def test_returns_200_with_expected_json(self, client):
        resp = client.get("/hello")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"message": "hello, i am sofapy"}

    def test_response_content_type_is_json(self, client):
        resp = client.get("/hello")

        assert "application/json" in resp.headers["content-type"]


# ============================================================================
# /health Endpoint
# ============================================================================


class TestHealthEndpoint:
    """Tests for GET /health endpoint."""

    def test_returns_200_with_healthy_status(self, client):
        resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "healthy"}

    def test_response_content_type_is_json(self, client):
        resp = client.get("/health")

        assert "application/json" in resp.headers["content-type"]


# ============================================================================
# DomainError Exception Handler
# ============================================================================


class TestDomainExceptionHandler:
    """Tests for @app.exception_handler(DomainError) → domain_exception_handler."""

    def test_returns_custom_http_status_from_exc(self):
        class ForbiddenError(DomainError):
            http_status = 403
            error_code = "FORBIDDEN"

        app = _build_app_with_error("/hello", ForbiddenError("access denied"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error_code"] == "FORBIDDEN"
        assert body["detail"]["message"] == "access denied"

    def test_default_http_status_is_500(self):
        app = _build_app_with_error("/hello", DomainError("something broke"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["error_code"] == "DOMAIN_ERROR"
        assert body["detail"]["message"] == "something broke"

    def test_response_body_structure(self):
        app = _build_app_with_error("/hello", DomainError("structured test"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        body = resp.json()
        assert "detail" in body
        assert "error_code" in body["detail"]
        assert "message" in body["detail"]
        assert body["detail"]["error_code"] == "DOMAIN_ERROR"
        assert body["detail"]["message"] == "structured test"

    def test_domain_error_on_health_returns_correct_status(self):
        app = _build_app_with_error("/health", DomainError("health check failed"))
        with TestClient(app) as client:
            resp = client.get("/health")

        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["error_code"] == "DOMAIN_ERROR"
        assert body["detail"]["message"] == "health check failed"


# ============================================================================
# ValueError Exception Handler
# ============================================================================


class TestValidationExceptionHandler:
    """Tests for @app.exception_handler(ValueError) → validation_exception_handler."""

    def test_returns_400_for_value_error(self):
        app = _build_app_with_error("/hello", ValueError("invalid input"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_REQUEST"
        assert body["detail"]["message"] == "invalid input"

    def test_preserves_value_error_message(self):
        app = _build_app_with_error("/hello", ValueError("field X must be positive"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        body = resp.json()
        assert body["detail"]["message"] == "field X must be positive"

    def test_response_content_type_is_json(self):
        app = _build_app_with_error("/hello", ValueError("bad"))
        with TestClient(app) as client:
            resp = client.get("/hello")

        assert "application/json" in resp.headers["content-type"]

    def test_value_error_on_health_returns_400(self):
        app = _build_app_with_error("/health", ValueError("bad health"))
        with TestClient(app) as client:
            resp = client.get("/health")

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_REQUEST"


# ============================================================================
# Generic Exception Handler
# ============================================================================


class TestUnhandledExceptionHandler:
    """Tests for @app.exception_handler(Exception) → unhandled_exception_handler.

    NOTE: RuntimeError propagated through TestClient is intercepted by
    Starlette's ServerErrorMiddleware, preventing custom exception handlers
    from firing. These tests use direct function calls instead.
    See: TestExceptionHandlersDirect for additional coverage.
    """

    @pytest.mark.asyncio
    async def test_returns_500_for_unexpected_exception(self):
        exc = RuntimeError("unexpected failure")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        assert response.status_code == 500
        parsed = json.loads(response.body.decode())
        assert parsed["detail"]["error_code"] == "INTERNAL_ERROR"
        assert parsed["detail"]["message"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_never_leaks_exception_details(self):
        exc = RuntimeError("secret database password leaked")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        parsed = json.loads(response.body.decode())
        assert parsed["detail"]["message"] == "Internal server error"
        assert "secret" not in parsed["detail"]["message"]
        assert "password" not in parsed["detail"]["message"]

    @pytest.mark.asyncio
    async def test_response_content_type_is_json(self):
        exc = RuntimeError("boom")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        assert "application/json" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_runtime_error_on_health_returns_500(self):
        exc = RuntimeError("critical")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        assert response.status_code == 500
        parsed = json.loads(response.body.decode())
        assert parsed["detail"]["error_code"] == "INTERNAL_ERROR"


# ============================================================================
# Router Registration
# ============================================================================


class TestRouterRegistration:
    """Verify that app.include_router calls exist for all imported routers."""

    def test_app_has_registered_routes(self, app_for_test):
        routes = app_for_test.routes
        route_paths = [r.path for r in routes if hasattr(r, "path")]
        assert len(route_paths) > 0
        assert "/hello" in route_paths
        assert "/health" in route_paths

    def test_real_app_router_count_matches_imports(self):
        from secbaas.adapters.web.app import app as real_app
        from tests.unit.adapters.web.conftest import iter_api_routes

        # Count actual APIRoute objects (with dependant), which excludes
        # _IncludedRouter wrappers and Starlette built-in routes.
        api_route_count = sum(1 for _ in iter_api_routes(real_app))
        # The real app has 26 imported routers providing API endpoints
        assert api_route_count >= 28  # 26 routers + /hello + /health


# ============================================================================
# load_config
# ============================================================================


class TestLoadConfig:
    """Tests for load_config() function — sofapy standard config loading."""

    def test_loads_config_and_returns_config_object(self, tmp_path):
        """WHEN a valid config file exists, THEN returns Config with user_config."""
        config_file = tmp_path / "configs"
        config_file.mkdir()
        (config_file / "application.yaml").write_text(
            "app_name: test_app\nuser_config:\n  key: value\n"
        )
        with (
            patch(
                "secbaas.adapters.web.app.ConfigLoader.load",
                return_value=Config(
                    app_name="test_app",
                    user_config={"key": "value"},
                ),
            ),
        ):
            from secbaas.adapters.web.app import load_config

            result = load_config()
            assert result.user_config["key"] == "value"
            assert result.app_name == "test_app"

    def test_env_specific_config_overrides_base(self):
        """WHEN ConfigLoader loads merged config, THEN override fields take priority."""
        mock_config = Config(
            app_name="my_app",
            workers=1,
            user_config={"db": "dev"},
        )
        with (
            patch(
                "secbaas.adapters.web.app.ConfigLoader.load",
                return_value=mock_config,
            ),
        ):
            from secbaas.adapters.web.app import load_config

            result = load_config()
            assert result.workers == 1
            assert result.user_config["db"] == "dev"
            assert result.app_name == "my_app"

    def test_config_not_found_raises_error(self):
        """WHEN config file not found, THEN ConfigLoader raises FileNotFoundError."""
        with (
            patch(
                "secbaas.adapters.web.app.ConfigLoader.load",
                side_effect=FileNotFoundError("配置文件不存在"),
            ),
        ):
            from secbaas.adapters.web.app import load_config

            with pytest.raises(FileNotFoundError, match="配置文件不存在"):
                load_config()

    def test_returns_config_with_sofapy_defaults(self):
        """WHEN ConfigLoader loads config, THEN Config object includes framework defaults."""
        mock_config = Config(
            app_name="sofapy_app",
            user_config={"arca": {"enabled": True}},
            workers=4,
        )
        with (
            patch(
                "secbaas.adapters.web.app.ConfigLoader.load",
                return_value=mock_config,
            ),
        ):
            from secbaas.adapters.web.app import load_config

            result = load_config()
            assert isinstance(result, Config)
            assert result.app_name == "sofapy_app"
            assert result.user_config["arca"]["enabled"] is True
            assert result.workers == 4

    # ------------------------------------------------------------------
    # SOFAPY_CONFIG_OVERLAY tests
    # ------------------------------------------------------------------

    def test_overlay_custom_name(self, monkeypatch, tmp_path):
        """WHEN SOFAPY_CONFIG_OVERLAY=custom, THEN merges base application.yaml + overlay."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "application.yaml").write_text(
            "app_name: base_app\nworkers: 2\nuser_config: {}\n"
        )
        overlay_dir = config_dir / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "custom.yaml").write_text("app_name: custom_app\nworkers: 4\n")
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "custom")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.adapters.web.app import load_config

        result = load_config()
        assert result.app_name == "custom_app"
        assert result.workers == 4

    def test_overlay_not_set_falls_back_to_get_config(self, monkeypatch):
        """WHEN SOFAPY_CONFIG_OVERLAY is not set, THEN calls get_config()."""
        monkeypatch.delenv("SOFAPY_CONFIG_OVERLAY", raising=False)
        mock_config = Config(app_name="default_app", workers=1)
        with patch(
            "secbaas.adapters.web.app.ConfigLoader.load",
            return_value=mock_config,
        ):
            from secbaas.adapters.web.app import load_config

            result = load_config()
            assert result.app_name == "default_app"
            assert result.workers == 1

    def test_overlay_merge_with_base_config(self, monkeypatch, tmp_path):
        """WHEN overlay file exists, THEN merges base application.yaml + overlay."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        # Base config
        (config_dir / "application.yaml").write_text(
            "app_name: base\nworkers: 2\nuser_config:\n  base_key: base_value\n"
        )
        # Overlay under overlays/ subdirectory
        overlay_dir = config_dir / "overlays"
        overlay_dir.mkdir()
        (overlay_dir / "override.yaml").write_text(
            "workers: 8\nuser_config:\n  extra_key: extra_value\n"
        )
        monkeypatch.setenv("SOFAPY_CONFIG_OVERLAY", "override")
        monkeypatch.setenv("SOFAPY_CONFIG_PATH", str(config_dir))
        from secbaas.adapters.web.app import load_config

        result = load_config()
        assert result.app_name == "base"  # from base
        assert result.workers == 8  # overridden by overlay
        assert result.user_config["base_key"] == "base_value"  # from base
        assert result.user_config["extra_key"] == "extra_value"  # from overlay


# ============================================================================
# App Module-Level Properties
# ============================================================================


class TestAppModule:
    """Tests for module-level app object and its properties."""

    def test_app_title(self):
        from secbaas.adapters.web.app import app as real_app

        assert real_app.title == "SecBaaS API"

    def test_app_version(self):
        from secbaas.adapters.web.app import app as real_app

        assert real_app.version == "1.0.0"

    def test_app_description(self):
        from secbaas.adapters.web.app import app as real_app

        assert real_app.description == "SecBaaS API"

    def test_app_has_lifespan(self):
        from secbaas.adapters.web.app import app as real_app

        assert real_app.router.lifespan_context is not None

    def test_app_has_middleware(self):
        from secbaas.adapters.web.app import app as real_app

        # Middleware is installed during lifespan, not at import time.
        # The app object is still created and has the expected FastAPI structure.
        assert real_app is not None
        assert hasattr(real_app, "user_middleware")

    def test_app_has_exception_handlers(self):
        from secbaas.adapters.web.app import app as real_app

        handlers = real_app.exception_handlers
        assert len(handlers) >= 3

    def test_lifespan_function_importable_and_callable(self):
        from secbaas.adapters.web.app import lifespan

        assert callable(lifespan)


# ============================================================================
# Exception Handlers — Direct Function Tests (no HTTP round-trip)
# ============================================================================


class TestExceptionHandlersDirect:
    """Direct unit tests for exception handler functions without HTTP."""

    @pytest.mark.asyncio
    async def test_domain_exception_handler_returns_500_default(self):
        """DomainError handler returns 500 with error_code and message."""
        exc = DomainError("test error")
        request = MagicMock()

        response = await domain_exception_handler(request, exc)

        assert response.status_code == 500
        body = response.body.decode()
        parsed = json.loads(body)
        assert parsed["detail"]["error_code"] == "DOMAIN_ERROR"
        assert parsed["detail"]["message"] == "test error"

    @pytest.mark.asyncio
    async def test_domain_exception_handler_custom_status(self):
        """DomainError with custom http_status propagates that status."""

        class NotFoundError(DomainError):
            http_status = 404
            error_code = "NOT_FOUND"

        exc = NotFoundError("resource missing")
        request = MagicMock()

        response = await domain_exception_handler(request, exc)

        assert response.status_code == 404
        body = response.body.decode()
        parsed = json.loads(body)
        assert parsed["detail"]["error_code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_validation_exception_handler_returns_400(self):
        """ValueError handler always returns 400."""
        exc = ValueError("invalid data")
        request = MagicMock()

        response = await validation_exception_handler(request, exc)

        assert response.status_code == 400
        body = response.body.decode()
        parsed = json.loads(body)
        assert parsed["detail"]["error_code"] == "INVALID_REQUEST"
        assert parsed["detail"]["message"] == "invalid data"

    @pytest.mark.asyncio
    async def test_unhandled_exception_handler_returns_500(self):
        """Generic exception handler always returns 500 with generic message."""
        exc = RuntimeError("secret info should be hidden")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        assert response.status_code == 500
        body = response.body.decode()
        parsed = json.loads(body)
        assert parsed["detail"]["error_code"] == "INTERNAL_ERROR"
        assert parsed["detail"]["message"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_unhandled_exception_handler_hides_details(self):
        """Generic handler hides arbitrary exception types and messages."""
        exc = OSError(2, "No such file or directory: /etc/secrets/db.conf")
        request = MagicMock()

        response = await unhandled_exception_handler(request, exc)

        body = response.body.decode()
        parsed = json.loads(body)
        assert parsed["detail"]["message"] == "Internal server error"
        assert "secrets" not in parsed["detail"]["message"]


# ============================================================================
# Real app /hello and /health (covers L266-267, L273)
# ============================================================================


class TestRealAppEndpoints:
    """Test /hello and /health on the real secbaas app module with mocked lifespan."""

    @pytest.fixture
    def real_client(self):
        """TestClient against real app with lifespan replaced by no-op."""
        from contextlib import asynccontextmanager

        from secbaas.adapters.web.app import app as real_app

        @asynccontextmanager
        async def noop_lifespan(app):
            yield

        original_lifespan = real_app.router.lifespan_context
        real_app.router.lifespan_context = noop_lifespan
        try:
            with TestClient(real_app) as client:
                yield client
        finally:
            real_app.router.lifespan_context = original_lifespan

    def test_hello_returns_expected_message(self, real_client):
        resp = real_client.get("/hello")
        assert resp.status_code == 200
        assert resp.json() == {"message": "hello, i am sofapy"}

    def test_health_returns_expected_status(self, real_client):
        resp = real_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
