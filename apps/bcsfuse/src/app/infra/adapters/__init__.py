"""
Infrastructure Adapters Package - OSS Version

Worker Registry 相关的 Adapter 实现。

OSS 版本只包含 OSS 兼容的适配器，不包含 ZDAS 相关的内部适配器。
"""

from app.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from app.infra.adapters.in_memory_worker_runtime_state_store import (
    InMemoryWorkerRuntimeStateStore,
)
from app.infra.adapters.in_memory_worker_profile_binding_store import (
    InMemoryWorkerProfileBindingStore,
)
from app.infra.adapters.in_memory_worker_audit_log_store import (
    InMemoryWorkerAuditLogStore,
)
from app.infra.adapters.in_memory_worker_index_sync_adapter import (
    InMemoryWorkerIndexSyncAdapter,
)
from app.infra.adapters.in_memory_fused_profile_store import (
    InMemoryFusedProfileStore,
)
from app.infra.adapters.sqlite_fused_profile_store import (
    SQLiteFusedProfileStore,
)
# OSS: ZDAS adapters removed - not compatible with open source


__all__ = [
    "InMemoryWorkerRegistryStore",
    "InMemoryWorkerRuntimeStateStore",
    "InMemoryWorkerProfileBindingStore",
    "InMemoryWorkerAuditLogStore",
    "InMemoryWorkerIndexSyncAdapter",
    "InMemoryFusedProfileStore",
    "SQLiteFusedProfileStore",
]