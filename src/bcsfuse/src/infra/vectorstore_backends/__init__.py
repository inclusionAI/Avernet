"""Vector store persistence backends (Open-Core Safe)."""

# Open-Core: 仅导出 public-safe backends
# ZDAS backends have moved to bcsfuse_internal.providers.vector

__all__ = [
    "SQLiteVectorPersistenceBackend",
]


def __getattr__(name):
    """延迟加载 persistence backend 类"""
    if name == "SQLiteVectorPersistenceBackend":
        from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import SQLiteVectorPersistenceBackend
        return SQLiteVectorPersistenceBackend
    elif name == "ZdasVectorPersistenceBackend":
        # ZDAS backend - internal only
        raise ImportError(
            f"{name} is internal-only and has moved to bcsfuse_internal.providers.vector. "
            f"Open-core must use SQLiteVectorPersistenceBackend or a public persistence backend. "
            f"For ZDAS support, use bcsfuse_internal provider wiring."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")