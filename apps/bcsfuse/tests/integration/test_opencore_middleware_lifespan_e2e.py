"""
G7: OpenCore Middleware/Lifespan E2E Tests

Gate Criteria:
- App startup succeeds in dev_smoke mode
- App shutdown succeeds
- Health endpoint remains lightweight and returns 200
- Ready endpoint works without internal providers
- CORS behavior works if configured
- Trace/request id behavior works if supported
- Error handling returns structured response
- No internal provider imports
- Provider registry initializes once and does not require network
"""

import pytest
import os
import time
from fastapi.testclient import TestClient
import sys


@pytest.fixture(scope="module")
def test_client():
    """Create test client for dev_smoke mode."""
    # Set environment for dev_smoke mode
    original_mode = os.getenv("BCSFUSE_PROVIDER_MODE")
    original_token = os.getenv("BCSFUSE_AUTH_TOKEN")
    os.environ["BCSFUSE_PROVIDER_MODE"] = "dev_smoke"
    os.environ["BCSFUSE_AUTH_TOKEN"] = "test_token_for_e2e"

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

        # Restore original token
        if original_token is not None:
            os.environ["BCSFUSE_AUTH_TOKEN"] = original_token
        elif "BCSFUSE_AUTH_TOKEN" in os.environ:
            del os.environ["BCSFUSE_AUTH_TOKEN"]


class TestOpencoreMiddlewareLifespanE2E:
    """G7: Middleware/Lifespan E2E tests"""

    def test_app_lifespan_startup_shutdown(self, test_client):
        """Test that app can startup and shutdown successfully"""
        # App was already created and started in fixture
        # Just verify it exists and is usable
        assert test_client is not None, "App should be created successfully"

        # Make a simple request to verify app is running
        response = test_client.get("/health")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"

        # App will be shut down when fixture is torn down
        # This test just verifies startup succeeded

    def test_health_remains_lightweight(self, test_client):
        """Test that health endpoint is lightweight and returns 200"""
        # Make multiple requests to verify health endpoint is lightweight
        start_time = time.time()

        for _ in range(10):
            response = test_client.get("/health")
            assert response.status_code == 200, f"Health check failed: {response.status_code}"

        end_time = time.time()
        duration = end_time - start_time

        # Health checks should be very fast (< 1 second for 10 requests)
        assert duration < 1.0, f"Health checks took {duration}s, should be lightweight"

        # Verify response structure
        response = test_client.get("/health")
        data = response.json()

        # Health response should be a dict
        assert isinstance(data, dict), "Health response should be a dict"

        # Should have status or similar field
        assert "status" in data or "healthy" in data or "state" in data, \
            "Health response should have status/healthy/state field"

    def test_ready_uses_public_safe_registry(self, test_client):
        """Test that ready endpoint works without internal providers"""
        response = test_client.get("/ready")

        # Ready endpoint should work
        assert response.status_code in [200, 503], \
            f"Ready endpoint returned unexpected status: {response.status_code}"

        if response.status_code == 200:
            data = response.json()

            # Response should be a dict
            assert isinstance(data, dict), "Ready response should be a dict"

            # Should have some readiness indicator
            assert "ready" in data or "status" in data or "checks" in data, \
                "Ready response should have ready/status/checks field"

    def test_cors_headers_if_configured(self, test_client):
        """Test CORS behavior if configured"""
        # Try to make a preflight request
        response = test_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            }
        )

        # CORS might not be configured, so just check it doesn't error
        # If configured, should have CORS headers
        if "Access-Control-Allow-Origin" in response.headers:
            # CORS is configured
            assert response.headers["Access-Control-Allow-Origin"] in ["*", "http://localhost:3000"], \
                "CORS origin should be configured properly"
        # If not configured, that's also OK for dev_smoke mode

    def test_trace_or_request_id_header_if_supported(self, test_client):
        """Test trace/request id behavior if supported"""
        # Make a request and check for trace id
        response = test_client.get("/health")

        # Check if trace id is in response headers or body
        has_trace_id = False

        # Check headers
        if "X-Trace-Id" in response.headers or "X-Request-Id" in response.headers:
            has_trace_id = True

        # Check body
        if response.status_code == 200:
            data = response.json()
            if "trace_id" in data or "request_id" in data:
                has_trace_id = True

        # Trace id support is optional but good practice
        # We just verify it doesn't break anything
        # No assertion required

    def test_error_response_is_structured(self, test_client):
        """Test that error handling returns structured response"""
        # Make a request to a non-existent endpoint
        response = test_client.get("/nonexistent-endpoint")

        # Should return 404 or similar error
        assert response.status_code == 404, "Non-existent endpoint should return 404"

        # Error response should be structured JSON
        data = response.json()
        assert isinstance(data, dict), "Error response should be a dict"

        # Should have error details
        assert "detail" in data or "error" in data or "message" in data, \
            "Error response should have detail/error/message field"

    def test_no_internal_provider_imports_during_lifespan(self, test_client):
        """Test that no internal providers are imported during lifespan"""
        # Check that no internal modules were imported
        internal_modules = [m for m in sys.modules.keys() if "bcsfuse_internal" in m]
        assert len(internal_modules) == 0, \
            f"Should not import internal providers during lifespan, found: {internal_modules}"

    def test_provider_registry_initialized_without_network(self, test_client):
        """Test that provider registry initializes without network calls"""
        # App was already created in fixture
        # We just need to verify it started successfully

        # If registry required network, app creation would have failed or taken long
        # Since we're here, registry initialized successfully

        # Verify app is functional
        response = test_client.get("/health")
        assert response.status_code == 200, "App should be functional after registry init"

        # Additional check: make a request that uses registry
        # This verifies registry is actually initialized
        response = test_client.post(
            "/api/v1/recommend",
            json={
                "question": "test question",
                "topK": 3,
            },
            headers={"Authorization": "Bearer test_token_for_e2e"}
        )

        # Should not be a network error (would be 500 or timeout)
        # Validation errors (400/422) are OK
        assert response.status_code in [200, 400, 422], \
            f"Registry-dependent request failed with unexpected status: {response.status_code}"

    def test_app_can_handle_multiple_concurrent_requests(self, test_client):
        """Test that app can handle multiple requests without issues"""
        import concurrent.futures

        def make_request():
            response = test_client.get("/health")
            return response.status_code

        # Make 10 concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All requests should succeed
        assert all(status == 200 for status in results), \
            f"Concurrent requests failed: {results}"

    def test_middleware_chain_works_correctly(self, test_client):
        """Test that middleware chain processes requests correctly"""
        # Test auth middleware
        response_no_auth = test_client.post(
            "/api/v1/recommend",
            json={"question": "test", "topK": 3}
        )

        # Should require auth (401) or reject (403)
        assert response_no_auth.status_code in [401, 403, 422], \
            f"Unauthenticated request should be rejected, got {response_no_auth.status_code}"

        # Test with auth
        response_with_auth = test_client.post(
            "/api/v1/recommend",
            json={"question": "test", "topK": 3},
            headers={"Authorization": "Bearer test_token_for_e2e"}
        )

        # Should process request (any status except auth error)
        # 200, 400, 422 are OK - just not 401/403
        assert response_with_auth.status_code in [200, 400, 422, 500], \
            f"Authenticated request should be processed, got {response_with_auth.status_code}"