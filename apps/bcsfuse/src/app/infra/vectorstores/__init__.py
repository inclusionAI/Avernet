"""Vector store implementations (Open-Core Safe)."""

# 延迟导入，避免在缺少可选依赖时导入失败
# Open-Core: 仅导出 public-safe vector stores
# ZDAS vector stores have moved to bcsfuse_internal.providers.vector

__all__ = []


def __getattr__(name):
    if name == "FaissZdasVectorStore":
        # ZDAS vector store - internal only
        raise ImportError(
            f"{name} is internal-only and has moved to bcsfuse_internal.providers.vector. "
            f"Open-core must use QdrantSQLiteVectorStore or a public vector store provider. "
            f"For ZDAS support, use bcsfuse_internal provider wiring."
        )
    elif name == "QdrantZdasVectorStore":
        # ZDAS vector store - internal only
        raise ImportError(
            f"{name} is internal-only and has moved to bcsfuse_internal.providers.vector. "
            f"Open-core must use QdrantLocalVectorStore (from app.infra.public.vectorstores) "
            f"or a public vector store provider. For ZDAS support, use bcsfuse_internal provider wiring."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")