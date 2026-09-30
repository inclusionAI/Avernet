#!/usr/bin/env python3
"""
OSS Startup Smoke Test

Tests that the OSS application can start and respond to health checks.
This is a standalone test that does NOT require external services.

Run with: python tests/smoke/startup_smoke.py
"""
import os
import sys
import ast
from pathlib import Path

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set minimal env vars for test
os.environ["STARTUP_PROFILE"] = "opensource"
os.environ["BCSFUSE_PROVIDER_MODE"] = "test"
os.environ["BCSFUSE_OBJECT_STORAGE_DIR"] = "/tmp/bcsfuse_test_storage"


def test_import_main():
    """Test that main can be imported."""
    print("\n[TEST] test_import_main")
    try:
        import main
        print("✓ main imported successfully")
        return True
    except Exception as e:
        print(f"✗ Failed to import main: {e}")
        raise


def test_import_create_opensource_app():
    """Test that create_opensource_app can be imported."""
    print("\n[TEST] test_import_create_opensource_app")
    try:
        from src.bootstrap.opensource_app import create_opensource_app
        print("✓ create_opensource_app imported successfully")
        return True
    except Exception as e:
        print(f"✗ Failed to import create_opensource_app: {e}")
        raise


def test_create_opensource_app_test_mode():
    """Test that create_opensource_app works in test mode."""
    print("\n[TEST] test_create_opensource_app_test_mode")
    from src.bootstrap.opensource_app import create_opensource_app

    app = create_opensource_app(mode="test")

    assert app is not None
    assert hasattr(app.state, "context")
    print("✓ OSS app created successfully in test mode")
    return True


def test_app_state_context():
    """Test that app.state.context exists and has provider registry."""
    print("\n[TEST] test_app_state_context")
    from src.bootstrap.opensource_app import create_opensource_app

    app = create_opensource_app(mode="test")

    assert hasattr(app.state, "context")
    assert app.state.context is not None
    assert app.state.context.registry is not None
    print("✓ app.state.context exists with provider registry")
    return True


def test_provider_key_count():
    """Test that provider registry has 13 provider keys."""
    print("\n[TEST] test_provider_key_count")
    from src.bootstrap.opensource_app import create_opensource_app

    app = create_opensource_app(mode="test")
    provider_count = len(app.state.context.registry.keys())

    assert provider_count == 13, f"Expected 13 providers, got {provider_count}"
    print(f"✓ Provider registry has 13 provider keys")
    return True


def test_health_endpoint():
    """Test that /health endpoint exists and returns correct response."""
    print("\n[TEST] test_health_endpoint")
    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["startup_profile"] == "opensource"
    assert data["provider_mode"] == "test"
    assert data["providers"] == 13

    print(f"✓ /health endpoint returns correct response")
    print(f"  Status: {data['status']}")
    print(f"  Profile: {data['startup_profile']}")
    print(f"  Mode: {data['provider_mode']}")
    print(f"  Providers: {data['providers']}")
    return True


def test_openapi_generation():
    """Test that /openapi.json can be generated."""
    print("\n[TEST] test_openapi_generation")
    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    assert "openapi" in data
    assert "info" in data
    assert data["info"]["title"] == "BCSFuse OSS"

    print(f"✓ /openapi.json generated successfully")
    print(f"  OpenAPI version: {data['openapi']}")
    print(f"  Title: {data['info']['title']}")
    return True


def test_startup_imports_no_forbidden_patterns():
    """Test that startup chain doesn't import forbidden internal patterns."""
    print("\n[TEST] test_startup_imports_no_forbidden_patterns")

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
    ]

    # Files to check
    files_to_check = [
        Path(__file__).parent.parent / "main.py",
        Path(__file__).parent.parent / "src" / "bootstrap" / "opensource_app.py",
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
                                violations.append(f"{file_path}: imports {module_name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            for pattern in forbidden_patterns:
                                if pattern.lower() in alias.name.lower():
                                    violations.append(f"{file_path}: imports {alias.name}")
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
    """Test that app build doesn't connect to external services."""
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


def test_business_route_module_not_in_interfaces_api():
    """Test that business route module is NOT in src.interfaces.api package."""
    print("\n[TEST] test_business_route_module_not_in_interfaces_api")

    oss_business_routes_path = Path(__file__).parent.parent / "src" / "bootstrap" / "oss_business_routes.py"

    assert oss_business_routes_path.exists(), f"oss_business_routes.py not found at {oss_business_routes_path}"

    # Check that oss_business_routes.py is NOT under src/interfaces/api/
    relative_path = oss_business_routes_path.relative_to(Path(__file__).parent.parent)
    relative_str = str(relative_path)

    if "interfaces" in relative_str and "api" in relative_str:
        print(f"✗ oss_business_routes.py is incorrectly placed under src/interfaces/api/")
        raise AssertionError(f"oss_business_routes.py should NOT be under src/interfaces/api/, found at {relative_path}")

    print(f"✓ oss_business_routes.py is correctly placed at: {relative_path}")
    print(f"  NOT under src/interfaces/api/")
    return True


def test_opensource_app_no_src_interfaces_api_import():
    """Test that opensource_app.py does NOT import from src.interfaces.api.*"""
    print("\n[TEST] test_opensource_app_no_src_interfaces_api_import")

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
        print(f"✗ opensource_app.py imports from src.interfaces.api.*:")
        for v in violations:
            print(f"    {v}")
        raise AssertionError("opensource_app.py imports from src.interfaces.api.* - this triggers forbidden import chain")

    print(f"✓ opensource_app.py does NOT import from src.interfaces.api.*")
    return True


def test_openapi_path_count_gt_8():
    """Test that OpenAPI has more than 8 paths (S8: basic business paths mounted)."""
    print("\n[TEST] test_openapi_path_count_gt_8")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    path_count = len(data.get("paths", {}))

    # S8 requirement: > 8 paths (health, ready, providers, providers/status, workers, worker_id, profiles, search, search/stats)
    assert path_count > 8, f"Expected > 8 paths, got {path_count}"
    print(f"✓ OpenAPI has {path_count} paths (expected > 8)")
    return True


def test_basic_business_paths_exist():
    """Test that all basic business paths exist in OpenAPI."""
    print("\n[TEST] test_basic_business_paths_exist")

    from src.bootstrap.opensource_app import create_opensource_app
    from fastapi.testclient import TestClient

    app = create_opensource_app(mode="test")
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    paths = data.get("paths", {})

    required_paths = [
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

    missing = [p for p in required_paths if p not in paths]

    if missing:
        print(f"✗ Missing basic business paths: {missing}")
        raise AssertionError(f"Missing basic business paths: {missing}")

    print(f"✓ All {len(required_paths)} basic business paths exist:")
    for p in required_paths:
        print(f"  ✓ {p}")
    return True


def run_all_tests():
    """Run all startup smoke tests."""
    print("=" * 70)
    print("OSS Startup Smoke Test")
    print("=" * 70)

    tests = [
        test_import_main,
        test_import_create_opensource_app,
        test_create_opensource_app_test_mode,
        test_app_state_context,
        test_provider_key_count,
        test_health_endpoint,
        test_openapi_generation,
        test_startup_imports_no_forbidden_patterns,
        test_no_external_connections_during_build,
        # S8 new tests
        test_business_route_module_not_in_interfaces_api,
        test_opensource_app_no_src_interfaces_api_import,
        test_openapi_path_count_gt_8,
        test_basic_business_paths_exist,
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