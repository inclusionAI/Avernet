"""ZdasVectorPersistenceBackend (Open-Core Stub - Internal Only)

For open-core, use SQLiteVectorPersistenceBackend from src.infra.vectorstore_backends.
"""

from __future__ import annotations


class ZdasInternalOnlyProviderUnavailable(RuntimeError):
    pass


class ZdasVectorPersistenceBackend:
    def __init__(self, *args, **kwargs):
        raise ZdasInternalOnlyProviderUnavailable(
            "ZdasVectorPersistenceBackend is internal-only. Open-core must use "
            "SQLiteVectorPersistenceBackend. For ZDAS support, use bcsfuse_internal providers."
        )
