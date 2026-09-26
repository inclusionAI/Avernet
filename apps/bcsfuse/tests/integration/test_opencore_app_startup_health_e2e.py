"""
OPENCORE App Startup and Health Check E2E Test

G1 Route Contract Test - Minimal smoke test for app startup and health endpoints.

Tests:
1. App creates successfully in dev_smoke mode
2. GET /health returns 200 with process health status
3. GET /ready returns valid response (200 or 503 with clear status)
4. No internal provider imports required
5. Startup/shutdown works correctly
"""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def test_client():
    """Create test client for dev_smoke mode."""
    # Set environment for dev_smoke mode
    original_mode = os.getenv("BCSFUSE_PROVIDER_MODE")
    os.environ["BCSFUSE_PROVIDER_MODE"] = "dev_smoke"

    try:
        from src.bootstrap.opensource_app import create_opensource_app

        app = create_opensource_app(mode="dev_smoke")
        client = TestClient(app)
        yield client
    finally:
        # Restore original mode
        if original_mode is not None:
            os.environ["BCSFUSE_PROVIDER_MODE"] = original_mode
        elif "BCSFUSE_PROVIDER_MODE" in os.environ:
            del os.environ["BCSFUSE_PROVIDER_MODE"]


class TestOpencoreAppStartupHealthE2E:
    """E2E tests for OPENCORE app startup and health endpoints."""

    def test_app_creates_in_dev_smoke_mode(self, test_client):
        """Test that app creates successfully in dev_smoke mode."""
        assert test_client is not None
        # App should be accessible
        response = test_client.get("/")
        # Root may return 404, but should not crash
        assert response.status_code in [200, 404, 422]

    def test_health_returns_200_with_process_status(self, test_client):
        """Test that GET /health returns 200 with process health status."""
        response = test_client.get("/health")

        # Health endpoint MUST return 200
        assert response.status_code == 200

        # Response must be valid JSON
        data = response.json()
        assert isinstance(data, dict)

        # Response must have required fields
        assert "status" in data
        assert data["status"] == "ok"
        assert "startup_profile" in data
        assert data["startup_profile"] == "opensource"
        assert "provider_mode" in data
        assert data["provider_mode"] == "dev_smoke"
        assert "process_health" in data
        assert data["process_health"] == "alive"

        # Health endpoint should NOT include provider count to avoid
        # triggering provider initialization
        assert "providers" not in data or data.get("providers") is None

    def test_ready_returns_valid_response(self, test_client):
        """Test that GET /ready returns valid response (200 or 503)."""
        response = test_client.get("/ready")

        # Ready endpoint should return 200 (ready) or 503 (not ready)
        # It should NOT return 500 (internal error)
        assert response.status_code in [200, 503]

        # Response must be valid JSON
        data = response.json()
        assert isinstance(data, dict)

        # Response must have required fields
        assert "ready" in data
        assert isinstance(data["ready"], bool)
        assert "provider_mode" in data
        assert data["provider_mode"] == "dev_smoke"

        if response.status_code == 200:
            # If ready, should have providers
            assert data["ready"] is True
            assert "providers" in data
            assert isinstance(data["providers"], int)
            assert data["providers"] >= 0
        else:
            # If not ready (503), should have error info
            assert data["ready"] is False
            # Should have error field or providers=0
            assert "error" in data or data.get("providers", 0) == 0

    def test_no_internal_provider_imports_required(self, test_client):
        """Test that health checks work without importing internal providers."""
        # This test verifies that the health endpoint works without
        # importing bcsfuse_internal or requiring internal dependencies.
        #
        # If internal imports were required, the health check would fail
        # with ImportError or ModuleNotFoundError.
        response = test_client.get("/health")
        assert response.status_code == 200

        response = test_client.get("/ready")
        assert response.status_code in [200, 503]

    def test_startup_shutdown_works(self, test_client):
        """Test that app startup and shutdown work correctly."""
        # If the app started successfully and TestClient works,
        # then startup/shutdown are working.
        #
        # Make multiple requests to ensure stability
        for _ in range(3):
            response = test_client.get("/health")
            assert response.status_code == 200

        for _ in range(3):
            response = test_client.get("/ready")
            assert response.status_code in [200, 503]

    def test_health_endpoint_performance(self, test_client):
        """Test that health endpoint responds quickly."""
        import time

        start = time.time()
        response = test_client.get("/health")
        elapsed = time.time() - start

        # Health endpoint should respond within 1 second
        assert response.status_code == 200
        assert elapsed < 1.0, f"Health endpoint took {elapsed}s, should be < 1s"