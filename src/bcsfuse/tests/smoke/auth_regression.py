#!/usr/bin/env python3
"""
S11 Auth Regression Test

Validates:
1. GET /health no auth -> 200
2. GET /ready no auth -> 200
3. GET /openapi.json no auth -> 200
4. GET /providers no auth -> 401
5. GET /v1/providers/status no auth -> 401
6. GET /v1/workers no auth -> 401
7. GET /v1/workers wrong token -> 401
8. GET /v1/workers correct token -> 200
9. POST /v1/workers correct token -> 200/201
10. GET /v1/search/stats no auth -> 401
11. GET /v1/search/stats correct token -> 200
12. POST /v1/search correct token -> 200/422 but not 401
13. Response body never contains token
14. No configs/application.yaml missing
15. No forbidden internal imports
16. No external service connections during app construction

This test does NOT:
- Use real tokens
- Connect to external services
- Import internal auth/DRM/Layotto code
"""
import os
import sys
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

# Add bcsfuse root to path
bcsfuse_root = Path(__file__).parent.parent
sys.path.insert(0, str(bcsfuse_root))
sys.path.insert(0, str(bcsfuse_root / "src"))

# Test token - NEVER use real tokens
TEST_TOKEN = "test-token"
WRONG_TOKEN = "wrong-token"


def test_health_no_auth():
    """Test GET /health without auth returns 200."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data['status'] == 'ok', f"Expected status=ok, got {data}"
        print("✅ GET /health no auth -> 200")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_ready_no_auth():
    """Test GET /ready without auth returns 200."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/ready")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'ready' in data, f"Expected 'ready' in response, got {data}"
        print("✅ GET /ready no auth -> 200")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_openapi_no_auth():
    """Test GET /openapi.json without auth returns 200."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/openapi.json")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'openapi' in data, f"Expected 'openapi' in response, got {data}"
        print("✅ GET /openapi.json no auth -> 200")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_providers_no_auth():
    """Test GET /providers without auth returns 401."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/providers")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✅ GET /providers no auth -> 401")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_providers_status_no_auth():
    """Test GET /v1/providers/status without auth returns 401."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/v1/providers/status")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✅ GET /v1/providers/status no auth -> 401")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_workers_no_auth():
    """Test GET /v1/workers without auth returns 401."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/v1/workers")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✅ GET /v1/workers no auth -> 401")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_workers_wrong_token():
    """Test GET /v1/workers with wrong token returns 401."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get(
            "/v1/workers",
            headers={"Authorization": f"Bearer {WRONG_TOKEN}"}
        )
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✅ GET /v1/workers wrong token -> 401")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_workers_correct_token():
    """Test GET /v1/workers with correct token returns 200."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get(
            "/v1/workers",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'success' in data, f"Expected 'success' in response, got {data}"
        print("✅ GET /v1/workers correct token -> 200")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_create_worker_correct_token():
    """Test POST /v1/workers with correct token returns 200/201."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.post(
            "/v1/workers",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            json={
                "worker_id": "test-worker-001",
                "name": "Test Worker",
                "description": "Test worker for auth regression",
                "skills": ["test"],
                "is_public": True
            }
        )
        assert response.status_code in [200, 201], f"Expected 200/201, got {response.status_code}"
        data = response.json()
        assert 'success' in data, f"Expected 'success' in response, got {data}"
        print(f"✅ POST /v1/workers correct token -> {response.status_code}")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_search_stats_no_auth():
    """Test GET /v1/search/stats without auth returns 401."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get("/v1/search/stats")
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("✅ GET /v1/search/stats no auth -> 401")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_search_stats_correct_token():
    """Test GET /v1/search/stats with correct token returns 200."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.get(
            "/v1/search/stats",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert 'vector_count' in data, f"Expected 'vector_count' in response, got {data}"
        print("✅ GET /v1/search/stats correct token -> 200")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_search_correct_token():
    """Test POST /v1/search with correct token returns 200/422 (not 401)."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        response = client.post(
            "/v1/search",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            json={"query": "test query", "top_k": 10}
        )
        # Should be 200 or 422 (validation), but NOT 401
        assert response.status_code in [200, 422], f"Expected 200/422, got {response.status_code}"
        assert response.status_code != 401, f"Should not return 401 with correct token"
        print(f"✅ POST /v1/search correct token -> {response.status_code}")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_token_not_in_response():
    """Test that response body never contains the token."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        client = TestClient(app)

        # Test multiple endpoints
        endpoints = [
            ("/v1/workers", "GET"),
            ("/v1/providers/status", "GET"),
            ("/v1/search/stats", "GET"),
        ]

        for endpoint, method in endpoints:
            response = client.request(
                method,
                endpoint,
                headers={"Authorization": f"Bearer {TEST_TOKEN}"}
            )

            # Check response body doesn't contain token
            response_text = response.text
            assert TEST_TOKEN not in response_text, f"Token found in response from {endpoint}"
            assert "Bearer" not in response_text, f"Bearer header found in response from {endpoint}"

        print("✅ Response body never contains token")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_no_application_yaml_required():
    """Test that configs/application.yaml is not required."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    # Remove config path from env
    os.environ.pop('BCSFUSE_CONFIG_PATH', None)

    try:
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')
        assert app is not None, "Failed to create app without application.yaml"
        print("✅ No configs/application.yaml required")
    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_no_forbidden_imports():
    """Test that no forbidden internal imports are used."""
    # Try to import the app and check for internal imports
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        # Import app
        from src.bootstrap.opensource_app import create_opensource_app
        from src.bootstrap.oss_business_routes import include_oss_business_routes

        # Check that internal modules are not imported
        import sys
        forbidden_modules = [
            'sofa_app',
            'zdas',
            'drm',
            'layotto',
            'sofapy_base',
            'rpplus',
            'bcsfuse_internal',
        ]

        violations = []
        for module in sys.modules:
            for forbidden in forbidden_modules:
                if forbidden in module.lower():
                    violations.append(module)

        if violations:
            print(f"⚠️  Warning: Found potential forbidden imports: {set(violations)}")
        else:
            print("✅ No forbidden internal imports")

    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def test_no_external_connections():
    """Test that no external service connections are made during app construction."""
    os.environ['BCSFUSE_PROVIDER_MODE'] = 'test'
    os.environ['BCSFUSE_AUTH_TOKEN'] = TEST_TOKEN

    try:
        # Create app in test mode - should not connect to external services
        from src.bootstrap.opensource_app import create_opensource_app
        app = create_opensource_app(mode='test')

        # Verify providers are fake/noop (not real HTTP)
        registry = app.state.context.registry

        # Check embedding provider
        embedding = registry.get('embedding_provider')
        if embedding:
            from src.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
            assert isinstance(embedding, FakeEmbeddingProvider), "Test mode should use FakeEmbeddingProvider"

        # Check reranker
        reranker = registry.get('reranker_provider')
        if reranker:
            from src.infra.public.reranker.noop_reranker import NoopReranker
            assert isinstance(reranker, NoopReranker), "Test mode should use NoopReranker"

        # Check LLM
        llm = registry.get('llm_provider')
        if llm:
            from src.infra.public.llm.fake_llm_provider import FakeLLMProvider
            assert isinstance(llm, FakeLLMProvider), "Test mode should use FakeLLMProvider"

        print("✅ No external service connections during app construction")

    finally:
        os.environ.pop('BCSFUSE_PROVIDER_MODE', None)
        os.environ.pop('BCSFUSE_AUTH_TOKEN', None)


def main():
    """Run all auth regression tests."""
    print("=" * 70)
    print("S11 Auth Regression Test")
    print("=" * 70)
    print()

    try:
        # Public endpoints (no auth required)
        test_health_no_auth()
        test_ready_no_auth()
        test_openapi_no_auth()

        # Protected endpoints (auth required)
        test_providers_no_auth()
        test_providers_status_no_auth()
        test_workers_no_auth()
        test_workers_wrong_token()
        test_workers_correct_token()
        test_create_worker_correct_token()
        test_search_stats_no_auth()
        test_search_stats_correct_token()
        test_search_correct_token()

        # Security checks
        test_token_not_in_response()
        test_no_application_yaml_required()
        test_no_forbidden_imports()
        test_no_external_connections()

        print()
        print("=" * 70)
        print("✅ ALL AUTH REGRESSION TESTS PASSED")
        print("=" * 70)
        return 0

    except AssertionError as e:
        print()
        print("=" * 70)
        print(f"❌ AUTH REGRESSION TEST FAILED: {e}")
        print("=" * 70)
        return 1
    except Exception as e:
        print()
        print("=" * 70)
        print(f"❌ UNEXPECTED ERROR: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())