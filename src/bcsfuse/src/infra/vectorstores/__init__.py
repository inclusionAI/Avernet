"""Vector store implementations (Open-Core Safe)."""

# 延迟导入，避免在缺少可选依赖时导入失败
# Open-Core: 仅导出 public-safe vector stores
# ZDAS vector stores have moved to bcsfuse_internal.providers.vector

__all__ = [
    "FaissVectorStoreAdapter",
    "FaissSqliteVectorStore",
]


def __getattr__(name):
    """延迟加载 vector store 类"""
    if name == "FaissVectorStoreAdapter":
        from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter
        return FaissVectorStoreAdapter
    elif name == "FaissSqliteVectorStore":
        from src.infra.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore
        return FaissSqliteVectorStore
    elif name == "FaissZdasVectorStore":
        # ZDAS vector store - internal only
        raise ImportError(
            f"{name} is internal-only and has moved to bcsfuse_internal.providers.vector. "
            f"Open-core must use FaissSqliteVectorStore or a public vector store provider. "
            f"For ZDAS support, use bcsfuse_internal provider wiring."
        )
    elif name == "QdrantZdasVectorStore":
        # ZDAS vector store - internal only
        raise ImportError(
            f"{name} is internal-only and has moved to bcsfuse_internal.providers.vector. "
            f"Open-core must use QdrantLocalVectorStore (from src.infra.public.vectorstores) "
            f"or a public vector store provider. For ZDAS support, use bcsfuse_internal provider wiring."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")