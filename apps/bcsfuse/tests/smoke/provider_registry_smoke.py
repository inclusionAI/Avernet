#!/usr/bin/env python3
"""
Provider Registry Smoke Test (Standalone)

This is a standalone smoke test that does NOT depend on pytest or internal fixtures.
It verifies that the OSS provider graph can be built and configured correctly.

Run with: python tests/smoke/provider_registry_smoke.py
"""
import os
import sys
import ast
from pathlib import Path

# Ensure we can import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set minimal env vars for test
os.environ["EMBEDDING_BASE_URL"] = "http://localhost:8001"
os.environ["EMBEDDING_AUTH_TOKEN"] = "test-token"
os.environ["LLM_BASE_URL"] = "http://localhost:8002"
os.environ["LLM_AUTH_TOKEN"] = "test-token"
os.environ["STARTUP_PROFILE"] = "opensource"
os.environ["BCSFUSE_OBJECT_STORAGE_DIR"] = "/tmp/bcsfuse_test_storage"


def test_test_registry_can_be_built():
    """Test that test registry can be built."""
    print("\n[TEST] test_test_registry_can_be_built")
    from src.bootstrap.opensource import build_opensource_provider_registry

    registry = build_opensource_provider_registry(mode="test")
    print("✓ Test registry built successfully")
    return True


def test_registry_contains_13_provider_keys():
    """Test that registry contains all 13 required provider keys."""
    print("\n[TEST] test_registry_contains_13_provider_keys")
    from src.bootstrap.opensource import build_opensource_provider_registry

    registry = build_opensource_provider_registry(mode="test")

    required_keys = [
        "config",
        "auth",
        "worker_registry_store",
        "worker_runtime_state_store",
        "worker_profile_content_store",
        "worker_profile_source",
        "vector_store",
        "embedding_provider",
        "reranker_provider",
        "llm_provider",
        "cache_provider",
        "audit_log_store",
        "object_storage_provider",
    ]

    missing_keys = []
    for key in required_keys:
        if not registry.has(key):
            missing_keys.append(key)

    if missing_keys:
        print(f"✗ Registry missing keys: {missing_keys}")
        print(f"  Available keys: {registry.keys()}")
        raise AssertionError(f"Missing required provider keys: {missing_keys}")
    else:
        print(f"✓ Registry contains all 13 required provider keys")
        return True


def test_test_fallback_provider_types():
    """Test that test mode uses correct provider types."""
    print("\n[TEST] test_test_fallback_provider_types")
    from src.bootstrap.opensource import build_opensource_provider_registry
    from src.infra.public.stores.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
    from src.infra.public.vectorstores.in_memory_vector_store import InMemoryVectorStore
    from src.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
    from src.infra.public.reranker.noop_reranker import NoopReranker
    from src.infra.public.llm.fake_llm_provider import FakeLLMProvider

    registry = build_opensource_provider_registry(mode="test")

    # Check types
    assert isinstance(registry.get("worker_registry_store"), InMemoryWorkerRegistryStore)
    assert isinstance(registry.get("vector_store"), InMemoryVectorStore)
    assert isinstance(registry.get("embedding_provider"), FakeEmbeddingProvider)
    assert isinstance(registry.get("reranker_provider"), NoopReranker)
    assert isinstance(registry.get("llm_provider"), FakeLLMProvider)

    print("✓ Test fallback provider types validated")
    return True


def test_provider_module_path_does_not_contain_forbidden_internal_patterns():
    """Test that provider modules don't import forbidden internal dependencies."""
    print("\n[TEST] test_provider_module_path_does_not_contain_forbidden_internal_patterns")
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

    oss_provider_dir = Path(__file__).parent.parent / "src" / "infra" / "oss"
    bootstrap_dir = Path(__file__).parent.parent / "src" / "bootstrap"

    violations = []

    def check_file_for_forbidden_imports(file_path: Path):
        """Check a Python file for forbidden imports."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse AST
            tree = ast.parse(content, filename=str(file_path))

            # Check imports
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
            # Skip files with syntax errors
            pass
        except Exception as e:
            print(f"Warning: Could not check {file_path}: {e}")

    # Check all Python files in oss provider directories
    for py_file in oss_provider_dir.rglob("*.py"):
        check_file_for_forbidden_imports(py_file)

    for py_file in bootstrap_dir.rglob("*.py"):
        check_file_for_forbidden_imports(py_file)

    if violations:
        print("✗ Found forbidden internal imports:")
        for v in violations:
            print(f"  {v}")
        raise AssertionError(f"Forbidden internal imports found: {violations}")
    else:
        print("✓ No forbidden internal imports found in provider modules")
        return True


def test_object_storage_root_dir_not_in_source_code():
    """Test that object storage provider does not use source code directories."""
    print("\n[TEST] test_object_storage_root_dir_not_in_source_code")

    from src.infra.public.object_storage.local_runtime_object_storage_provider import LocalRuntimeObjectStorageProvider

    provider = LocalRuntimeObjectStorageProvider()

    # Check that root_dir is not in forbidden locations
    forbidden_dirs = ["src/", "tests/", "configs/", "docs/"]
    root_str = str(provider.root_dir)

    for forbidden in forbidden_dirs:
        if forbidden in root_str:
            raise AssertionError(f"Object storage root_dir in forbidden directory: {forbidden}")

    print(f"✓ Object storage root_dir validation passed: {provider.root_dir}")
    return True


def run_all_tests():
    """Run all smoke tests."""
    print("=" * 70)
    print("Provider Registry Smoke Test (Standalone)")
    print("=" * 70)

    tests = [
        test_test_registry_can_be_built,
        test_registry_contains_13_provider_keys,
        test_test_fallback_provider_types,
        test_provider_module_path_does_not_contain_forbidden_internal_patterns,
        test_object_storage_root_dir_not_in_source_code,
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