"""Composed embedding providers must reach lifecycle and search consumers."""

from src.bootstrap.app_factory import _configure_fusion_dependencies
from src.bootstrap.application_context import ApplicationContext
from src.bootstrap.provider_registry import ProviderRegistry
from src.interfaces.api.dependencies import fusion_dependencies


class FixedEmbedding:
    def __init__(self, value):
        self.value = value

    def embed(self, text):
        return self.value


def test_switching_compositions_replaces_then_clears_cached_embedding(monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    for vector in ([1.0, 0.0], [0.0, 1.0], None):
        registry = ProviderRegistry()
        provider = FixedEmbedding(vector) if vector else None
        if provider:
            registry.register("embedding_provider", provider)
        context = ApplicationContext(mode="test", startup_profile="acceptance", registry=registry)
        _configure_fusion_dependencies(context)
        resolved = fusion_dependencies._get_embedding_generator()
        if vector:
            assert resolved is provider
            assert resolved.embed("probe") == vector
        else:
            assert resolved is None


def test_composed_embedding_survives_service_cache_reset(monkeypatch):
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    registry = ProviderRegistry()
    provider = FixedEmbedding([1.0, 0.0])
    registry.register("embedding_provider", provider)
    context = ApplicationContext(mode="test", startup_profile="acceptance", registry=registry)
    _configure_fusion_dependencies(context)
    fusion_dependencies.reset_fusion_services()
    assert fusion_dependencies._get_embedding_generator() is provider


def test_active_context_replaces_cached_provider_without_reconfiguration():
    for vector in ([1.0, 0.0], [0.0, 1.0], None):
        registry = ProviderRegistry()
        provider = FixedEmbedding(vector) if vector else None
        if provider is not None:
            registry.register("embedding_provider", provider)
        fusion_dependencies.set_app_context(ApplicationContext(
            mode="test", startup_profile="acceptance", registry=registry,
        ))

        assert fusion_dependencies._get_embedding_generator() is provider


def test_composed_missing_embedding_never_uses_environment_provider(monkeypatch):
    from src.infra.embedding.config.embedding_settings import EmbeddingSettings

    environment_reads = []

    def reject_environment_read(*args, **kwargs):
        environment_reads.append(True)
        raise AssertionError("Composed provider resolution must not read environment settings")

    monkeypatch.setattr(EmbeddingSettings, "__init__", reject_environment_read)
    fusion_dependencies.set_app_context(ApplicationContext(
        mode="test", startup_profile="acceptance", registry=ProviderRegistry(),
    ))
    fusion_dependencies.reset_fusion_services()

    assert fusion_dependencies._get_embedding_generator() is None
    assert environment_reads == []
