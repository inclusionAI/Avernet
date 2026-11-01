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


def test_runtime_environment_template_defaults_to_qdrant_server() -> None:
    """The public runtime template must not reintroduce per-pod local locking."""
    from pathlib import Path

    template = Path(__file__).resolve().parents[2] / ".env.example"
    rendered = template.read_text(encoding="utf-8")
    runtime_lines = [
        line
        for line in rendered.splitlines()
        if "VECTOR_BACKEND" in line or "QDRANT_" in line
    ]
    runtime_config = "\n".join(runtime_lines)
    assert 'export QDRANT_MODE="server"' in runtime_config
    assert 'export QDRANT_URL="change_me"' in runtime_config
    assert 'export QDRANT_KEYWORD_URL="change_me"' in runtime_config
    assert 'export QDRANT_PREFER_GRPC="true"' in runtime_config
    assert 'export QDRANT_KEYWORD_PREFER_GRPC="true"' in runtime_config
    assert 'export VECTOR_BACKEND="qdrant_server"' in runtime_config
    assert "QDRANT_LOCAL_PATH" not in runtime_config

    import yaml

    config_path = Path(__file__).resolve().parents[2] / "configs" / "application.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["vector"]["runtime_default"] == "qdrant_server"
