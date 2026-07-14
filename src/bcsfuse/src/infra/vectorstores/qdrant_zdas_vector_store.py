"""
QdrantZdasVectorStore (Open-Core Stub - Internal Only)

This module is a stub for open-core. The real implementation is internal-only
and has moved to bcsfuse_internal.providers.vector.

For open-core, use QdrantLocalVectorStore from src.infra.public.vectorstores.
"""

from __future__ import annotations


class ZdasInternalOnlyProviderUnavailable(RuntimeError):
    """Raised when attempting to use ZDAS provider in open-core."""
    pass


class QdrantZdasVectorStore:
    """Stub - Internal only. Use QdrantLocalVectorStore for open-core."""

    def __init__(self, *args, **kwargs):
        raise ZdasInternalOnlyProviderUnavailable(
            "QdrantZdasVectorStore is internal-only and has moved to "
            "bcsfuse_internal.providers.vector. Open-core must use "
            "QdrantLocalVectorStore from src.infra.public.vectorstores. "
            "For ZDAS support, use bcsfuse_internal provider wiring."
        )


def get_qdrant_zdas_vector_store(*args, **kwargs):
    """Stub - Internal only."""
    raise ZdasInternalOnlyProviderUnavailable(
        "get_qdrant_zdas_vector_store is internal-only. Open-core must use "
        "QdrantLocalVectorStore from src.infra.public.vectorstores. "
        "For ZDAS support, use bcsfuse_internal provider wiring."
    )