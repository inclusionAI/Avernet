"""Public TC composition selects a Qdrant Server without external model credentials."""

from app.bootstrap.opensource import build_opensource_provider_registry
from search_core.config.qdrant import QdrantConnection


def test_direct_smoke_defaults_to_server_and_honors_explicit_url(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.tc import providers

    monkeypatch.delenv("QDRANT_MODE", raising=False)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setenv(
        "WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "workers.sqlite")
    )
    captured = []
    monkeypatch.setattr(
        providers,
        "QdrantRetrievalIndex",
        lambda **kwargs: captured.append(kwargs) or object(),
    )

    def connection():
        registry = build_opensource_provider_registry(mode="dev_smoke")
        assert not registry.has("tc_index")  # construction is lazy
        providers.storage(
            SimpleNamespace(
                registry=registry, config=registry.require("config"), mode="dev_smoke"
            )
        )
        return captured[-1]["connection"]

    default = connection()
    assert default.mode == "server"
    assert default.url == "http://127.0.0.1:6333"
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:16333")
    assert connection().url == "http://127.0.0.1:16333"


def test_connection_mapping_keeps_auth_out_of_repr():
    connection = QdrantConnection.from_mapping(
        {
            "QDRANT_URL": "http://127.0.0.1:6333",
            "QDRANT_API_KEY": "test-token",
            "QDRANT_PREFER_GRPC": "false",
        },
        default_mode="server",
    )
    assert connection.mode == "server"
    assert not connection.prefer_grpc
    assert "test-token" not in repr(connection)
