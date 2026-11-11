"""TC composition of the original stores; selected only for v1 legacy mode."""

import os
from contextlib import nullcontext
from pathlib import Path

from search_core.config.qdrant import QdrantConnection


def storage(context):
    registry, config = context.registry, context.config
    factory = registry.get("tc_legacy_storage_factory")
    if factory is not None:
        return factory()
    dimension = registry.require("embedding_provider").dimension
    if context.mode in {"test", "dev", "dev_smoke"}:
        from app.infra.public.stores.sqlite_worker_profile_content_store import (
            SQLiteWorkerProfileContentStore,
        )
        from app.infra.public.stores.sqlite_worker_registry_store import (
            SQLiteWorkerRegistryStore,
        )
        from app.infra.public.stores.sqlite_worker_runtime_state_store import (
            SQLiteWorkerRuntimeStateStore,
        )
        from app.infra.public.vectorstores.qdrant_sqlite_vector_store import (
            QdrantSQLiteVectorStore,
        )

        path = (
            os.getenv("BCSFUSE_DATABASE_SQLITE_PATH")
            or os.getenv("WORKER_REGISTRY_SQLITE_DB_PATH")
            or config.get("database.sqlite.path", "data/bcsfuse.db")
        )
        temporary = registry.get("tc_temporary_directory")
        if path == ":memory:" or (
            temporary is not None and not os.getenv("BCSFUSE_DATABASE_SQLITE_PATH")
        ):
            path = str(Path(temporary.name) / "legacy.sqlite")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        vector_path = os.getenv("BCSFUSE_VECTOR_SQLITE_PATH") or path
        index_path = (os.getenv("QDRANT_LOCAL_PATH") or path + ".qdrant") + "-legacy"
        connection = (
            QdrantConnection(mode="local")
            if context.mode == "test"
            else QdrantConnection.from_mapping(os.environ, default_mode="local")
        )
        return (
            SQLiteWorkerRegistryStore(path),
            SQLiteWorkerProfileContentStore(path),
            SQLiteWorkerRuntimeStateStore(path),
            QdrantSQLiteVectorStore(
                dimension=dimension,
                db_path=vector_path,
                qdrant_path=index_path,
                auto_load=False,
                connection=connection,
            ),
            nullcontext,  # Local SQLite is a single-instance development backend.
        )
    if context.mode != "runtime":
        raise ValueError("internal TC legacy mode requires tc_legacy_storage_factory")
    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )
    from app.infra.public.stores.mysql_worker_profile_content_store import (
        MySQLWorkerProfileContentStore,
    )
    from app.infra.public.stores.mysql_worker_registry_store import (
        MySQLWorkerRegistryStore,
    )
    from app.infra.public.stores.mysql_worker_runtime_state_store import (
        MySQLWorkerRuntimeStateStore,
    )
    from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
        QdrantMySQLVectorStore,
    )
    from app.tc.legacy_transaction import LegacyMutationPool

    pool = LegacyMutationPool(
        registry.get("mysql_connection_pool") or MySQLConnectionPoolProvider()
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
    return (
        MySQLWorkerRegistryStore(connection_pool=pool),
        MySQLWorkerProfileContentStore(connection_pool=pool),
        MySQLWorkerRuntimeStateStore(connection_pool=pool),
        QdrantMySQLVectorStore(
            dimension=dimension,
            collection_name=os.getenv("QDRANT_COLLECTION_NAME", "worker_profiles"),
            qdrant_path=(os.getenv("QDRANT_LOCAL_PATH") or "data/qdrant") + "-legacy",
            connection=connection,
        ),
        pool.transaction,
    )
