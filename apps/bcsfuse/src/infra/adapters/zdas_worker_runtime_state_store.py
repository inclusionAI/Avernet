"""zdas_worker_runtime_state_store (Open-Core Stub - Internal Only)

For open-core, use SQLite/MySQL adapters from src.infra.adapters.
ZDAS is an internal-only data access service not available in open-source.
"""

from __future__ import annotations


class ZdasInternalOnlyProviderUnavailable(RuntimeError):
    """Raised when attempting to use ZDAS provider in open-core."""
    pass


class ZdasWorkerRuntimeStateStore:
    """Stub - Internal only. Use SQLite/MySQL adapters for open-core."""

    def __init__(self, *args, **kwargs):
        raise ZdasInternalOnlyProviderUnavailable(
            "ZdasWorkerRuntimeStateStore is internal-only and not available in open-core. "
            "Use SQLite or MySQL adapters from src.infra.adapters instead."
        )
