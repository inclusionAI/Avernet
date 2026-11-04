"""Composition selects storage explicitly; direct local startup stays offline."""

from search_core.config.qdrant import QdrantConnection

from app.bootstrap.opensource import build_opensource_provider_registry


def test_direct_smoke_defaults_to_local_and_accepts_explicit_server(
    tmp_path, monkeypatch
):
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

    assert connection().mode == "local"
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    assert connection().mode == "server"


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
