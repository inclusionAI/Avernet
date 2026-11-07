"""TC local development: SQLite durable records and rebuildable Qdrant indexes."""

from pathlib import Path
from tempfile import TemporaryDirectory

from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection

from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from app.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


class QdrantSQLiteVectorStore(QdrantMySQLVectorStore):
    def __init__(
        self,
        dimension: int = 4096,
        db_path: str = ":memory:",
        *,
        qdrant_path: str | None = None,
        auto_load: bool = True,
        connection: QdrantConnection = LOCAL_QDRANT,
    ):
        self._db_path = db_path
        self._temporary_index = None
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if qdrant_path is None:
            if db_path == ":memory:":
                self._temporary_index = TemporaryDirectory(prefix="bcsfuse-qdrant-")
                qdrant_path = self._temporary_index.name
            else:
                qdrant_path = db_path + ".qdrant"
        super().__init__(
            dimension=dimension,
            connection=connection,
            qdrant_path=qdrant_path,
            persistence_backend=SQLiteVectorPersistenceBackend(db_path=db_path),
        )
        if auto_load:
            try:
                self.rebuild_from_backend()
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._temporary_index is not None:
                self._temporary_index.cleanup()
                self._temporary_index = None

    def save_snapshot(self, path: str) -> None:
        """Export durable records; indexes are regenerated when loading."""
        import sqlite3

        target = Path(path) / "vector_embeddings.sqlite"
        if (
            self._db_path != ":memory:"
            and target.resolve() == Path(self._db_path).resolve()
        ):
            raise ValueError("snapshot destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._index_lock:
            connection = sqlite3.connect(str(target))
            try:
                self._persistence._conn.backup(connection)
            finally:
                connection.close()

    def load_snapshot(self, path: str) -> None:
        source = Path(path) / "vector_embeddings.sqlite"
        if not source.is_file():
            raise FileNotFoundError(source)
        import json
        import math
        import sqlite3
        import numpy as np
        from app.domain.models.vector_point import VectorPoint

        # Read-only and strict: no schema creation, skipped corrupt rows or
        # legacy pickle deserialization when importing a snapshot artifact.
        connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT id, vector, payload FROM bcsfuse_vector_embeddings"
            )
            points = [
                VectorPoint(
                    id=key,
                    vector=np.frombuffer(blob, dtype=np.float32).tolist(),
                    payload=json.loads(payload),
                )
                for key, blob, payload in rows
            ]
        finally:
            connection.close()
        if any(
            len(p.vector) != self.dimension
            or not all(math.isfinite(v) for v in p.vector)
            for p in points
        ):
            raise ValueError("invalid snapshot vector dimension or values")
        with self._index_lock:
            self.clear()
            self.upsert(points)

    def clear(self) -> None:
        """Propagate deletion failures; the durable backend remains authoritative."""
        with self._index_lock:
            self._persistence.delete_batch([p.id for p in self._persistence.load_all()])
            self.rebuild_from_backend()

    def delete_by_profile(self, worker_id: str, profile_id: str) -> int:
        """Delete every fragment of the exact Worker/Profile, including durable orphans."""
        with self._index_lock:
            ids = [
                point.id
                for batch in self._persistence.iter_batches(256)
                for point in batch
                if point.payload.get("worker_id") == worker_id
                and point.payload.get("profile_id") == profile_id
            ]
            self.delete(ids)
            return len(ids)

    def stats(self) -> dict:
        with self._index_lock:
            workers = {
                point.payload["worker_id"]
                for batch in self._persistence.iter_batches(256)
                for point in batch
                if point.payload.get("worker_id")
            }
            return {
                "vector_count": self.size(),
                "dimension": self.dimension,
                "indexed_workers": len(workers),
            }
