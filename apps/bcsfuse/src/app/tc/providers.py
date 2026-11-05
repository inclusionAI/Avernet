"""Composition of public TC storage and models; no internal platform imports."""

import os
import tempfile
from pathlib import Path

from search_core.catalog.updates import configuration_index_key
from search_core.config.qdrant import QdrantConnection
from search_core.plugin_api.reranker import RerankResult
from search_core.providers.demo_embedding import DemoEmbedding
from search_core.providers.qdrant_index import QdrantRetrievalIndex
from search_core.providers.sql_catalog import (
    SqlCatalogRepository,
    sqlite_catalog_repository,
)

from app.tc.catalog import FILTER_FIELDS, TcDocumentConverter


class OfflineReranker:
    """Deterministic plumbing double, only composed in test/dev_smoke."""

    def rerank(self, query, candidates, top_k=5):
        return [
            RerankResult(c["id"], 1.0 - i / (len(candidates) + 1))
            for i, c in enumerate(candidates[:top_k])
        ]


def configure_models(registry, config, mode):
    if mode in {"test", "dev_smoke"}:
        from app.infra.public.llm.fake_llm_provider import FakeLLMProvider

        registry.register(
            "embedding_provider",
            DemoEmbedding(config.get_int("embedding.dimension", 256)),
        )
        registry.register("reranker_provider", OfflineReranker())
        registry.register("llm_provider", FakeLLMProvider())
    else:
        from app.bootstrap.opensource import register_runtime_service_providers

        register_runtime_service_providers(registry, config)


def embedding_identity(config, embedding):
    model = (
        getattr(embedding, "model", None)
        or os.getenv("EMBEDDING_MODEL")
        or config.get("embedding.model")
        or "tc-model"
    )
    return f"{model}/{embedding.dimension}"


def storage(context):
    registry, config = context.registry, context.config
    supplied = registry.get("tc_storage_factory")
    if supplied is not None:
        return supplied()
    repository = registry.get("tc_catalog_repository")
    index = registry.get("tc_index")
    if repository is not None and index is not None:
        return repository, index
    environment = config.get(
        "environment", "local" if context.mode != "runtime" else "prod"
    )
    embedding = registry.require("embedding_provider")
    temporary = None
    if context.mode in {"test", "dev", "dev_smoke"}:
        if context.mode in {"test", "dev_smoke"} and (
            not os.getenv("WORKER_REGISTRY_SQLITE_DB_PATH")
            or os.getenv("WORKER_REGISTRY_SQLITE_DB_PATH") == ":memory:"
        ):
            temporary = tempfile.TemporaryDirectory(prefix="tc-bcsfuse-")
            registry.register("tc_temporary_directory", temporary)
        default_dir = Path(temporary.name) if temporary else Path("data")
        path = os.getenv("WORKER_REGISTRY_SQLITE_DB_PATH") or config.get(
            "database.sqlite.path", str(default_dir / "tc_cards.sqlite3")
        )
        if path == ":memory:":
            path = str(default_dir / "tc_cards.sqlite3")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        repository = sqlite_catalog_repository(
            path,
            environment=environment,
        )
        connection = (
            QdrantConnection(mode="local")
            if context.mode == "test"
            else QdrantConnection.from_mapping(os.environ, default_mode="local")
        )
        index_path = os.getenv("QDRANT_LOCAL_PATH") or str(default_dir / "tc_qdrant")
        if not registry.has("fused_profile_store"):
            from app.infra.adapters.sqlite_fused_profile_store import (
                SQLiteFusedProfileStore,
            )

            registry.register(
                "fused_profile_store",
                SQLiteFusedProfileStore(
                    db_path=os.getenv("BCSFUSE_DATABASE_SQLITE_PATH") or path
                ),
            )
    elif context.mode == "runtime":
        from app.infra.public.database.mysql_connection_pool import (
            MySQLConnectionPoolProvider,
        )
        from app.infra.public.stores.mysql_fused_profile_store import (
            MySQLFusedProfileStore,
        )

        pool = registry.get("mysql_connection_pool") or MySQLConnectionPoolProvider()
        repository = SqlCatalogRepository(
            pool.get_connection,
            table="bcsfuse_agent_cards",
            document_table="search_index_documents",
            environment=environment,
            placeholder="%s",
        )
        if not registry.has("fused_profile_store"):
            registry.register(
                "fused_profile_store", MySQLFusedProfileStore(connection_pool=pool)
            )
        connection = QdrantConnection.from_mapping(
            {
                **os.environ,
                "QDRANT_URL": os.getenv("QDRANT_URL")
                or config.get("vector.qdrant.url")
                or "http://127.0.0.1:6333",
            },
            default_mode="server",
        )
        index_path = (
            os.getenv("QDRANT_LOCAL_PATH") or "data/tc_qdrant"
            if connection.mode == "local"
            else ""
        )
    else:
        raise ValueError(
            "internal TC requires an explicitly composed tc_storage_factory"
        )
    embedding_key = embedding_identity(config, embedding)
    key = configuration_index_key(embedding_key, FILTER_FIELDS, TcDocumentConverter())
    index = QdrantRetrievalIndex(
        path=index_path,
        dimension=embedding.dimension,
        collection_name=f"tc_{environment}_{key}",
        connection=connection,
    )
    return repository, index
