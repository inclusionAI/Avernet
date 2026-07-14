"""
Provider Contract Skeleton Tests

Tests to verify that provider protocols and public-safe providers are correctly defined
and don't import internal infrastructure.
"""
import pytest
import os


def test_provider_protocols_can_import():
    """Test that all provider protocols can be imported."""
    from src.application.ports import (
        StartupProvider,
        SecretProvider,
        ContextProvider,
        ConfigProvider,
        CacheProvider,
        AuthProvider,
        ObjectStorageProvider,
    )
    from src.application.ports import (
        EmbeddingProvider,
        LLMProvider,
        RerankerProvider,
        VectorStore,
        WorkerRegistryStore,
        WorkerProfileContentStore,
        AuditLogStore,
    )

    # Verify protocols are defined
    assert StartupProvider is not None
    assert SecretProvider is not None
    assert ContextProvider is not None
    assert ConfigProvider is not None
    assert CacheProvider is not None
    assert AuthProvider is not None
    assert ObjectStorageProvider is not None
    assert EmbeddingProvider is not None
    assert LLMProvider is not None
    assert RerankerProvider is not None
    assert VectorStore is not None


def test_public_providers_can_import():
    """Test that public-safe providers can be imported."""
    from src.providers.public import (
        NoopStartupProvider,
        EnvSecretProvider,
        NoopContextProvider,
        YamlEnvConfigProvider,
        InMemoryCacheProvider,
    )

    # Verify providers are defined
    assert NoopStartupProvider is not None
    assert EnvSecretProvider is not None
    assert NoopContextProvider is not None
    assert YamlEnvConfigProvider is not None
    assert InMemoryCacheProvider is not None


@pytest.mark.asyncio
async def test_noop_startup_provider_callable():
    """Test that NoopStartupProvider can initialize and shutdown."""
    from src.providers.public import NoopStartupProvider

    provider = NoopStartupProvider()

    # Should not raise exceptions
    await provider.initialize()
    await provider.shutdown()


def test_env_secret_provider_reads_env():
    """Test that EnvSecretProvider can read environment variables."""
    from src.providers.public import EnvSecretProvider

    # Set test secret
    os.environ["TEST_SECRET_KEY"] = "test_secret_value"

    provider = EnvSecretProvider()

    # Should read from environment
    value = provider.get_secret("TEST_SECRET_KEY")
    assert value == "test_secret_value"

    # Should return default if not found
    value = provider.get_secret("NONEXISTENT_KEY", default="default_value")
    assert value == "default_value"

    # Should raise if required and not found
    with pytest.raises(KeyError):
        provider.require_secret("DEFINITELY_NONEXISTENT_KEY_12345")

    # Cleanup
    del os.environ["TEST_SECRET_KEY"]


def test_env_secret_provider_with_prefix():
    """Test that EnvSecretProvider works with prefix."""
    from src.providers.public import EnvSecretProvider

    # Set test secret with prefix
    os.environ["MYAPP_TEST_SECRET"] = "secret_with_prefix"

    provider = EnvSecretProvider(prefix="MYAPP_")

    # Should read with prefix
    value = provider.get_secret("TEST_SECRET")
    assert value == "secret_with_prefix"

    # Cleanup
    del os.environ["MYAPP_TEST_SECRET"]


@pytest.mark.asyncio
async def test_noop_context_provider_returns_empty_dict():
    """Test that NoopContextProvider returns empty context."""
    from src.providers.public import NoopContextProvider

    provider = NoopContextProvider()

    # Should return empty dict
    context = await provider.get_context()
    assert context == {}

    # Should return empty dict even with group/user IDs
    context = await provider.get_context(group_id="test_group", user_id="test_user")
    assert context == {}

    # set_context should be no-op (not raise)
    await provider.set_context({"key": "value"})


def test_no_internal_infra_imports_in_protocols():
    """Test that provider protocols don't import internal infrastructure."""
    import sys

    # Check that forbidden modules are not loaded
    forbidden_modules = [
        'sofapy',
        'layotto',
        'mist',
        'bcn',
        'zdas',
        'oceanbase',
        'mosn',
        'bcsfuse_internal',
    ]

    for module_name in sys.modules:
        for forbidden in forbidden_modules:
            assert forbidden not in module_name.lower(), \
                f"Provider protocol imported forbidden module: {module_name}"


def test_no_internal_infra_imports_in_public_providers():
    """Test that public providers don't import internal infrastructure."""
    import sys

    # Import public providers
    from src.providers.public import (
        NoopStartupProvider,
        EnvSecretProvider,
        NoopContextProvider,
    )

    # Check that forbidden modules are not loaded
    forbidden_modules = [
        'sofapy',
        'layotto',
        'mist',
        'bcn',
        'zdas',
        'oceanbase',
        'mosn',
        'bcsfuse_internal',
    ]

    for module_name in sys.modules:
        for forbidden in forbidden_modules:
            assert forbidden not in module_name.lower(), \
                f"Public provider imported forbidden module: {module_name}"


def test_open_core_does_not_import_bcsfuse_internal():
    """Test that open-core code doesn't import bcsfuse_internal."""
    import sys

    # Check that bcsfuse_internal is not loaded
    assert 'bcsfuse_internal' not in sys.modules, \
        "Open-core imported bcsfuse_internal package"

    for module_name in sys.modules:
        assert 'bcsfuse_internal' not in module_name, \
            f"Open-core imported module from bcsfuse_internal: {module_name}"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])