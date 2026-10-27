"""Composition selects storage explicitly; direct local startup stays offline."""

from search_core.config.qdrant import QdrantConnection

from app.bootstrap.opensource import build_opensource_provider_registry


def test_direct_smoke_defaults_to_local_and_accepts_explicit_server(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("QDRANT_MODE", raising=False)
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setenv("BCSFUSE_VECTOR_SQLITE_PATH", str(tmp_path / "vectors.sqlite"))
    monkeypatch.setenv(
        "WORKER_REGISTRY_SQLITE_DB_PATH", str(tmp_path / "workers.sqlite")
    )
    local = build_opensource_provider_registry(mode="dev_smoke").require("vector_store")
    assert local.connection.mode == "local"
    local.close()
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    server = build_opensource_provider_registry(mode="dev_smoke").require(
        "vector_store"
    )
    assert server.connection.mode == "server"
    # Composition stays lazy; startup is responsible for probing/rebuilding.
    assert server._qdrant._client is None
    assert server._keywords._client is None
    server.close()


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
