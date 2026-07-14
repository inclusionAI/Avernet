"""
OPENCORE Dev Smoke Provider Registry E2E Test

Diagnostic test to verify dev_smoke provider registry initialization.
"""
import os
import sys
import pytest


class TestDevSmokeProviderRegistryE2E:
    """
    Test dev_smoke provider registry initialization.

    OPENCORE-G1 route contract readiness gate depends on provider registry.
    """

    def test_create_application_context_dev_smoke(self):
        """Test that ApplicationContext can be created in dev_smoke mode."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")

        assert context is not None
        assert context.mode == "dev_smoke"

    def test_registry_initializes_without_internal_imports(self):
        """Test that provider registry initializes without internal imports."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")

        # Access registry - this triggers lazy initialization
        registry = context.registry

        assert registry is not None
        assert len(registry.keys()) > 0

    def test_registry_has_required_providers(self):
        """Test that registry has all required providers in dev_smoke mode."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")
        registry = context.registry

        # Check required providers
        required_providers = [
            "config",
            "drm_config",
            "auth",
            "cache_provider",
            "object_storage_provider",
            "worker_registry_store",
            "worker_runtime_state_store",
            "worker_profile_content_store",
            "audit_log_store",
            "vector_store",
            "embedding_provider",
            "reranker_provider",
            "llm_provider",
            "worker_profile_source",
        ]

        missing_providers = []
        for provider_name in required_providers:
            if not registry.has(provider_name):
                missing_providers.append(provider_name)

        assert len(missing_providers) == 0, f"Missing providers: {missing_providers}"

    def test_provider_types_are_public_safe(self):
        """Test that all providers in dev_smoke mode are public-safe."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")
        registry = context.registry

        # Check that no providers are from bcsfuse_internal
        for provider_name in registry.keys():
            provider = registry.get(provider_name)
            provider_module = type(provider).__module__

            assert "bcsfuse_internal" not in provider_module, \
                f"Provider {provider_name} is from internal module: {provider_module}"

    def test_no_external_network_required(self):
        """Test that dev_smoke providers don't require external network."""
        from src.bootstrap.application_context import build_application_context

        # This test should pass without network access
        context = build_application_context(mode="dev_smoke")
        registry = context.registry

        # Check that embedding and LLM providers are fake/noop
        embedding_provider = registry.get("embedding_provider")
        llm_provider = registry.get("llm_provider")
        reranker_provider = registry.get("reranker_provider")

        # These should be Fake/Noop types
        assert "Fake" in type(embedding_provider).__name__ or "Noop" in type(embedding_provider).__name__, \
            f"Embedding provider should be Fake/Noop, got: {type(embedding_provider).__name__}"

        assert "Fake" in type(llm_provider).__name__ or "Noop" in type(llm_provider).__name__, \
            f"LLM provider should be Fake/Noop, got: {type(llm_provider).__name__}"

        assert "Noop" in type(reranker_provider).__name__, \
            f"Reranker provider should be Noop, got: {type(reranker_provider).__name__}"

    def test_no_internal_package_import_attempted(self):
        """Test that no internal packages are imported during registry build."""
        from src.bootstrap.application_context import build_application_context

        # Track modules before
        modules_before = set(sys.modules.keys())

        # Build registry
        context = build_application_context(mode="dev_smoke")
        _ = context.registry

        # Track modules after
        modules_after = set(sys.modules.keys())

        # Check for internal module imports
        forbidden_modules = [
            "bcsfuse_internal",
            "ant_sofapy_base",
            "mist_sdk",
            "sofapy",
            "layotto",
        ]

        imported_forbidden = []
        for module in modules_after - modules_before:
            for forbidden in forbidden_modules:
                if forbidden in module:
                    imported_forbidden.append(module)

        # Allow internal imports in forbidden list if they're actually imported
        # (dev_smoke should NOT import them)
        assert len(imported_forbidden) == 0, \
            f"dev_smoke mode imported forbidden internal modules: {imported_forbidden}"

    def test_database_backend_is_sqlite_or_inmemory(self):
        """Test that database backend is SQLite or InMemory in dev_smoke mode."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")
        registry = context.registry

        # Check worker registry store type
        worker_store = registry.get("worker_registry_store")
        store_type = type(worker_store).__name__

        assert "SQLite" in store_type or "InMemory" in store_type, \
            f"Worker registry store should be SQLite or InMemory, got: {store_type}"

    def test_vector_backend_is_faiss_or_inmemory(self):
        """Test that vector backend is Faiss or InMemory in dev_smoke mode."""
        from src.bootstrap.application_context import build_application_context

        context = build_application_context(mode="dev_smoke")
        registry = context.registry

        # Check vector store type
        vector_store = registry.get("vector_store")
        store_type = type(vector_store).__name__

        assert "Faiss" in store_type or "InMemory" in store_type, \
            f"Vector store should be Faiss or InMemory, got: {store_type}"