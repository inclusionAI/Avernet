"""FaissZdasVectorStore (Open-Core Stub - Internal Only)

For open-core, use FaissSqliteVectorStore from src.infra.vectorstores.
"""

from __future__ import annotations


class ZdasInternalOnlyProviderUnavailable(RuntimeError):
    pass


class FaissZdasVectorStore:
    def __init__(self, *args, **kwargs):
        raise ZdasInternalOnlyProviderUnavailable(
            "FaissZdasVectorStore is internal-only. Open-core must use "
            "FaissSqliteVectorStore. For ZDAS support, use bcsfuse_internal providers."
        )