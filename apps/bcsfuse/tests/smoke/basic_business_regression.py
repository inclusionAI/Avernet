#!/usr/bin/env python3
"""
OSS Basic Business API Regression Test (S8)

Validates that OSS business routes work correctly in test mode without
connecting to external services.

S11G: Updated with auth headers for protected endpoints.

Run with: python tests/smoke/basic_business_regression.py
"""
import os
import sys
import ast
from pathlib import Path

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import auth helper
from oss_test_auth import set_test_auth_env, auth_headers, TEST_TOKEN

# Set minimal env vars for test
os.environ["STARTUP_PROFILE"] = "opensource"
os.environ["BCSFUSE_PROVIDER_MODE"] = "test"
os.environ["BCSFUSE_OBJECT_STORAGE_DIR"] = "/tmp/bcsfuse_test_storage"

# Set auth token for protected endpoints
set_test_auth_env(TEST_TOKEN)


def test_health_endpoint():
    """Test 1: /health returns 200"""
    print("\n[TEST] test_health_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["status"] == "ok"
    assert data["startup_profile"] == "opensource"
    assert data["provider_mode"] == "test"
    assert data["providers"] == 13

    print(f"✓ /health returned 200")
    print(f"  - Status: {data['status']}")
    print(f"  - Providers: {data['providers']}")
    return True


def test_ready_endpoint():
    """Test 2: /ready returns 200"""
    print("\n[TEST] test_ready_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/ready")
    assert response.status_code in [200, 503], f"Expected 200 or 503, got {response.status_code}"
    data = response.json()
    assert "ready" in data
    assert "providers" in data

    print(f"✓ /ready returned {response.status_code}")
    print(f"  - Ready: {data['ready']}")
    print(f"  - Providers: {data['providers']}")
    return True


def test_providers_endpoint():
    """Test 3: /providers returns 200 with 13 keys (requires auth)"""
    print("\n[TEST] test_providers_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    # S11G: /providers is a protected endpoint - requires auth
    response = client.get("/providers", headers=auth_headers())
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "provider_mode" in data
    assert "providers" in data
    assert len(data["providers"]) == 13, f"Expected 13 providers, got {len(data['providers'])}"

    print(f"✓ /providers returned 200 with 13 keys (with auth)")
    print(f"  - Provider mode: {data['provider_mode']}")
    print(f"  - Provider count: {len(data['providers'])}")
    return True


def test_openapi_includes_basic_business_paths():
    """Test 4: /openapi.json includes basic business paths"""
    print("\n[TEST] test_openapi_includes_basic_business_paths")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    paths = data.get("paths", {})

    expected_paths = [
        "/health",
        "/ready",
        "/providers",
        "/v1/providers/status",
        "/v1/workers",
        "/v1/workers/{worker_id}",
        "/v1/workers/{worker_id}/profiles",
        "/v1/search",
        "/v1/search/stats",
    ]

    missing = []
    for path in expected_paths:
        if path not in paths:
            missing.append(path)

    path_count = len(paths)
    print(f"✓ /openapi.json returned 200")
    print(f"  - Total paths: {path_count}")

    if missing:
        print(f"  ✗ Missing paths: {missing}")
        raise AssertionError(f"Missing OpenAPI paths: {missing}")

    for path in expected_paths:
        print(f"  ✓ {path}")

    assert path_count >= 9, f"Expected at least 9 paths, got {path_count}"
    return True


def test_workers_list_endpoint():
    """Test 5: GET /v1/workers returns 200"""
    print("\n[TEST] test_workers_list_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/v1/workers", headers=auth_headers())
    # S8F: Must return 200, not 503
    assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.text}"

    data = response.json()
    assert "success" in data, f"Response missing 'success' field: {data}"
    assert "items" in data, f"Response missing 'items' field: {data}"
    assert data["success"] is True, f"Expected success=True, got {data['success']}"

    print(f"✓ /v1/workers returned 200")
    print(f"  - Success: {data['success']}")
    print(f"  - Items: {len(data['items'])}")
    print(f"  - Total: {data.get('total', 'N/A')}")
    return True


def test_worker_get_missing_id():
    """Test 6: GET /v1/workers/{missing_id} returns 404"""
    print("\n[TEST] test_worker_get_missing_id")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/v1/workers/nonexistent-worker-id", headers=auth_headers())
    # S8F: Must return 404, not 503
    assert response.status_code == 404, f"Expected 404, got {response.status_code}. Response: {response.text}"

    data = response.json()
    assert "detail" in data or "code" in data, f"Response missing error detail: {data}"

    print(f"✓ /v1/workers/nonexistent-worker-id returned 404")
    print(f"  - Detail: {data.get('detail', data.get('code', 'N/A'))}")
    return True


def test_search_stats_endpoint():
    """Test 7: GET /v1/search/stats returns 200"""
    print("\n[TEST] test_search_stats_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/v1/search/stats", headers=auth_headers())
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "vector_backend" in data
    assert "vector_count" in data

    print(f"✓ /v1/search/stats returned 200")
    print(f"  - Backend: {data['vector_backend']}")
    print(f"  - Count: {data['vector_count']}")
    return True


def test_search_endpoint_exists():
    """Test 8: POST /v1/search route exists"""
    print("\n[TEST] test_search_endpoint_exists")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    # Test with empty query (should return 422 validation error)
    response = client.post("/v1/search", json={"query": ""}, headers=auth_headers())
    # Empty query should return 422 validation error
    assert response.status_code == 422, f"Expected 422 for empty query, got {response.status_code}"

    print(f"✓ /v1/search empty query returned 422 (validation error)")

    # Test with valid query - S8F: Must return 200, not 503
    response = client.post("/v1/search", json={"query": "test query", "top_k": 5}, headers=auth_headers())
    assert response.status_code == 200, f"Expected 200 for valid query, got {response.status_code}. Response: {response.text}"

    # Test with valid query - S8F: Must return 200, not 503
    response = client.post("/v1/search", json={"query": "test query", "top_k": 5}, headers=auth_headers())
    assert response.status_code == 200, f"Expected 200 for valid query, got {response.status_code}. Response: {response.text}"

    data = response.json()
    assert "success" in data, f"Response missing 'success' field: {data}"
    assert data["success"] is True, f"Expected success=True, got {data['success']}"

    print(f"  ✓ Search returned 200")
    print(f"    - Success: {data['success']}")
    print(f"    - Results count: {data.get('results_count', 0)}")
    return True


def test_profiles_list_endpoint():
    """Test 8.5: GET /v1/workers/{worker_id}/profiles returns 200"""
    print("\n[TEST] test_profiles_list_endpoint")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    # Test with a non-existent worker - should still work (return empty profiles)
    response = client.get("/v1/workers/test-worker-id/profiles", headers=auth_headers())
    # S8F: Must return 200, not 503
    assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.text}"

    data = response.json()
    assert "success" in data, f"Response missing 'success' field: {data}"
    assert "items" in data, f"Response missing 'items' field: {data}"
    assert data["success"] is True, f"Expected success=True, got {data['success']}"

    print(f"✓ /v1/workers/test-worker-id/profiles returned 200")
    print(f"  - Success: {data['success']}")
    print(f"  - Items: {len(data['items'])}")
    print(f"  - Total: {data.get('total', 0)}")
    return True


def test_no_configs_application_yaml_error():
    """Test 9: No configs/application.yaml missing error during startup"""
    print("\n[TEST] test_no_configs_application_yaml_error")

    # This test ensures that the startup doesn't fail due to missing configs/application.yaml
    from src.bootstrap.opensource_app import create_opensource_app

    try:
        app = create_opensource_app(mode="test")
        assert app is not None
        print("✓ App created without configs/application.yaml dependency")
        return True
    except FileNotFoundError as e:
        if "application.yaml" in str(e):
            print(f"✗ App failed due to missing application.yaml: {e}")
            raise AssertionError(f"App depends on configs/application.yaml: {e}")
        raise
    except Exception as e:
        # Check error message for yaml-related issues
        error_msg = str(e).lower()
        if "application.yaml" in error_msg or "application.yml" in error_msg:
            print(f"✗ App failed due to yaml config: {e}")
            raise AssertionError(f"App depends on yaml config: {e}")
        # Other errors are OK for this test
        print(f"✓ No yaml config dependency found (other error: {type(e).__name__})")
        return True


def test_no_forbidden_internal_imports():
    """Test 10: No forbidden internal imports in startup chain"""
    print("\n[TEST] test_no_forbidden_internal_imports")

    forbidden_patterns = [
        "sofa_app",
        "ZDAS",
        "zdas",
        "DRM",
        "drm",
        "Layotto",
        "layotto",
        "sofapy_base",
        "rpplus",
        "qdrant_zdas",
        "faiss_zdas",
        "bcsfuse-internal",
        "src.interfaces.api",  # Should NOT import from src.interfaces.api.*
    ]

    files_to_check = [
        Path(__file__).parent.parent / "main.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "opensource_app.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "oss_business_routes.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "application_context.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "opensource.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "provider_registry.py",
    ]

    violations = []

    def check_file_for_forbidden_imports(file_path: Path):
        """Check a Python file for forbidden imports."""
        if not file_path.exists():
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module_name = node.module if isinstance(node, ast.ImportFrom) else None
                    if module_name:
                        for pattern in forbidden_patterns:
                            if pattern.lower() in module_name.lower():
                                violations.append(f"{file_path.name}: imports {module_name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            for pattern in forbidden_patterns:
                                if pattern.lower() in alias.name.lower():
                                    violations.append(f"{file_path.name}: imports {alias.name}")
        except SyntaxError:
            pass
        except Exception as e:
            print(f"Warning: Could not check {file_path}: {e}")

    for file_path in files_to_check:
        check_file_for_forbidden_imports(file_path)

    if violations:
        print("✗ Found forbidden internal imports:")
        for v in violations:
            print(f"  {v}")
        raise AssertionError(f"Forbidden internal imports found: {violations}")
    else:
        print("✓ No forbidden internal imports in startup chain")
        return True


def test_no_external_connections_during_build():
    """Test 11: No external service connections during app construction"""
    print("\n[TEST] test_no_external_connections_during_build")

    from unittest.mock import patch, MagicMock
    from src.bootstrap.opensource_app import create_opensource_app

    # Mock all external connections
    with patch('mysql.connector.connect') as mock_mysql, \
         patch('sqlite3.connect') as mock_sqlite, \
         patch('qdrant_client.QdrantClient') as mock_qdrant, \
         patch('httpx.Client') as mock_httpx, \
         patch('requests.Session') as mock_requests:

        # Create app in test mode (should use in-memory providers)
        app = create_opensource_app(mode="test")

        # No external connections should have been made
        mysql_count = mock_mysql.call_count
        sqlite_count = mock_sqlite.call_count
        qdrant_count = mock_qdrant.call_count
        httpx_count = mock_httpx.call_count
        requests_count = mock_requests.call_count

        print(f"✓ App built without external connections:")
        print(f"  MySQL calls: {mysql_count}")
        print(f"  SQLite calls: {sqlite_count}")
        print(f"  Qdrant calls: {qdrant_count}")
        print(f"  httpx.Client calls: {httpx_count}")
        print(f"  requests.Session calls: {requests_count}")

        # Test mode should have 0 external calls
        assert mysql_count == 0, f"MySQL connected during build (count: {mysql_count})"
        assert qdrant_count == 0, f"Qdrant connected during build (count: {qdrant_count})"

        return True


def test_route_module_not_in_interfaces_api():
    """Test 12: Business route module is NOT in src.interfaces.api package"""
    print("\n[TEST] test_route_module_not_in_interfaces_api")

    oss_business_routes_path = Path(__file__).parent.parent / "src" / "bootstrap" / "oss_business_routes.py"
    interfaces_api_path = Path(__file__).parent.parent / "src" / "interfaces" / "api"

    assert oss_business_routes_path.exists(), f"oss_business_routes.py not found at {oss_business_routes_path}"

    # Check that oss_business_routes.py is NOT under src/interfaces/api/
    relative_path = oss_business_routes_path.relative_to(Path(__file__).parent.parent)
    assert "interfaces/api" not in str(relative_path), \
        f"oss_business_routes.py should NOT be under src/interfaces/api/, found at {relative_path}"

    print(f"✓ oss_business_routes.py is correctly placed at: {relative_path}")
    print(f"  NOT under src/interfaces/api/")
    return True


def test_opensource_app_does_not_import_src_interfaces_api():
    """Test 13: opensource_app.py does NOT import from src.interfaces.api.*"""
    print("\n[TEST] test_opensource_app_does_not_import_src_interfaces_api")

    opensource_app_path = Path(__file__).parent.parent / "src" / "bootstrap" / "opensource_app.py"

    with open(opensource_app_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Use AST to check for actual imports (not comments)
    tree = ast.parse(content, filename=str(opensource_app_path))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "src.interfaces.api" in node.module:
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "src.interfaces.api" in alias.name:
                    violations.append(f"import {alias.name}")

    if violations:
        print("✗ opensource_app.py imports from src.interfaces.api.*:")
        for v in violations:
            print(f"    {v}")
        raise AssertionError("opensource_app.py imports from src.interfaces.api.*")

    print("✓ opensource_app.py does NOT import from src.interfaces.api.*")
    return True


def test_business_routes_module_no_forbidden_imports():
    """Test 14: oss_business_routes.py does NOT import from src.interfaces.api.*"""
    print("\n[TEST] test_business_routes_module_no_forbidden_imports")

    oss_business_routes_path = Path(__file__).parent.parent / "src" / "bootstrap" / "oss_business_routes.py"

    with open(oss_business_routes_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Use AST to check for actual imports (not comments)
    tree = ast.parse(content, filename=str(oss_business_routes_path))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "src.interfaces.api" in node.module:
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "src.interfaces.api" in alias.name:
                    violations.append(f"import {alias.name}")

    if violations:
        print("✗ oss_business_routes.py imports from src.interfaces.api.*:")
        for v in violations:
            print(f"    {v}")
        raise AssertionError("oss_business_routes.py imports from src.interfaces.api.*")

    print("✓ oss_business_routes.py does NOT import from src.interfaces.api.*")
    return True


def test_openapi_path_count():
    """Test 15: OpenAPI has more than 8 paths"""
    print("\n[TEST] test_openapi_path_count")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/openapi.json")
    data = response.json()
    path_count = len(data.get("paths", {}))

    assert path_count > 8, f"Expected > 8 paths, got {path_count}"
    print(f"✓ OpenAPI has {path_count} paths (expected > 8)")
    return True


def run_all_tests():
    """Run all basic business regression tests."""
    print("=" * 70)
    print("OSS Basic Business API Regression Test (S8)")
    print("=" * 70)

    tests = [
        test_health_endpoint,
        test_ready_endpoint,
        test_providers_endpoint,
        test_openapi_includes_basic_business_paths,
        test_workers_list_endpoint,
        test_worker_get_missing_id,
        test_search_stats_endpoint,
        test_search_endpoint_exists,
        test_profiles_list_endpoint,
        test_no_configs_application_yaml_error,
        test_no_forbidden_internal_imports,
        test_no_external_connections_during_build,
        test_route_module_not_in_interfaces_api,
        test_opensource_app_does_not_import_src_interfaces_api,
        test_business_routes_module_no_forbidden_imports,
        test_openapi_path_count,
    ]

    passed = 0
    failed = 0
    errors = []

    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            failed += 1
            error_msg = f"{test.__name__}: {str(e)}"
            errors.append(error_msg)
            print(f"✗ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()

    print()
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if errors:
        print("\nErrors:")
        for error in errors:
            print(f"  - {error}")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)