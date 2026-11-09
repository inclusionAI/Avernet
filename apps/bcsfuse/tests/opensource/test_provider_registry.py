"""
Provider Registry Smoke Test

Tests that the OSS provider graph can be built and configured correctly.
"""

import ast
import os
from pathlib import Path


def test_provider_module_path_does_not_contain_forbidden_internal_patterns():
    """Test that provider modules don't import forbidden internal dependencies."""
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

    module_root = Path(__file__).resolve().parents[2]
    oss_provider_dir = module_root / "src/app/infra/public"
    bootstrap_dir = module_root / "src/app/bootstrap"

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
                    module_name = (
                        node.module if isinstance(node, ast.ImportFrom) else None
                    )
                    if module_name:
                        # This is our environment/YAML adapter, not the internal
                        # DRM SDK. Its own imports are scanned below as well.
                        if module_name == "app.infra.public.config.drm_config_provider":
                            continue
                        for pattern in forbidden_patterns:
                            if pattern.lower() in module_name.lower():
                                violations.append(f"{file_path}: imports {module_name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            for pattern in forbidden_patterns:
                                if pattern.lower() in alias.name.lower():
                                    violations.append(
                                        f"{file_path}: imports {alias.name}"
                                    )

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


def test_runtime_service_providers_can_be_added_without_replacing_storage(
    monkeypatch,
):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:8001")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8002")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-token")

    from app.bootstrap.opensource import (
        build_opensource_provider_registry,
        register_runtime_service_providers,
    )
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )
    from app.infra.public.embedding.real_embedding_provider import (
        RealEmbeddingProvider,
    )
    from app.infra.public.llm.anthropic_compatible_provider import (
        AnthropicCompatibleProvider,
    )

    registry = build_opensource_provider_registry(mode="test")
    original_store = registry.get("worker_registry_store")

    register_runtime_service_providers(registry, YamlEnvConfigProvider())

    assert registry.get("worker_registry_store") is original_store
    assert isinstance(registry.get("embedding_provider"), RealEmbeddingProvider)
    assert isinstance(registry.get("llm_provider"), AnthropicCompatibleProvider)


def test_runtime_service_providers_preserve_environment_model_selection(
    monkeypatch,
):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:8001")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8002")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("LLM_FAST_MODEL", "internal-fast")
    monkeypatch.setenv("LLM_REASONING_MODEL", "internal-reasoning")

    from app.bootstrap.opensource import (
        build_opensource_provider_registry,
        register_runtime_service_providers,
    )
    from app.infra.public.llm import anthropic_compatible_provider as llm_module

    captured = {}

    class CapturingProvider:
        def __init__(self, settings):
            captured["settings"] = settings

    monkeypatch.setattr(
        llm_module,
        "AnthropicCompatibleProvider",
        CapturingProvider,
    )

    class InternalStyleConfig:
        def get(self, key, default=None):
            return default

    registry = build_opensource_provider_registry(mode="test")
    register_runtime_service_providers(registry, InternalStyleConfig())

    physical_models = {
        profile.logical_model_id: profile.physical_model_name
        for profile in captured["settings"].model_registry
    }
    assert physical_models["fast.default"] == "internal-fast"
    assert physical_models["reasoning.default"] == "internal-reasoning"


def test_object_storage_root_dir_not_in_source_code():
    """Test that object storage provider does not use source code directories."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["BCSFUSE_OBJECT_STORAGE_DIR"] = tmpdir

        from app.infra.public.object_storage.local_runtime_object_storage_provider import (
            LocalRuntimeObjectStorageProvider,
        )

        provider = LocalRuntimeObjectStorageProvider()

        # Check that root_dir is not in forbidden locations
        forbidden_dirs = ["src/", "tests/", "configs/", "docs/"]
        root_str = str(provider.root_dir)

        for forbidden in forbidden_dirs:
            if forbidden in root_str:
                raise AssertionError(
                    f"Object storage root_dir in forbidden directory: {forbidden}"
                )

        print(f"✓ Object storage root_dir validation passed: {provider.root_dir}")
        return True


def test_current_registry_is_lazy_and_local_providers_are_public_safe():
    from search_core.providers.demo_embedding import DemoEmbedding

    from app.bootstrap.opensource import build_opensource_provider_registry

    for mode in ("dev", "runtime", "test", "dev_smoke"):
        registry = build_opensource_provider_registry(mode)
        assert registry.has("config") and registry.has("startup_provider")
        assert registry.get("tc_catalog_repository") is None
        assert registry.get("tc_index") is None
        if mode in {"test", "dev_smoke"}:
            assert isinstance(registry.require("embedding_provider"), DemoEmbedding)
        else:
            assert callable(registry.require("tc_model_factory"))
