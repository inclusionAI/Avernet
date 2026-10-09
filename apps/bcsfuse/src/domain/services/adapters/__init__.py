"""
Domain Adapters Package

Worker Registry 相关的 Adapter 接口定义。

Stage 1 Phase 1 只定义 Protocol，不做具体实现（SQLite 等）。

Phase D: 添加 Evidence Adapters（Legacy Signal -> Evidence 转换）
"""

from src.domain.services.adapters.worker_registry_store_adapter import (
    WorkerRegistryStoreAdapter,
)
from src.domain.services.adapters.worker_runtime_state_store_adapter import (
    WorkerRuntimeStateStoreAdapter,
)
from src.domain.services.adapters.worker_profile_binding_store_adapter import (
    WorkerProfileBindingStoreAdapter,
)
from src.domain.services.adapters.worker_audit_log_adapter import (
    WorkerAuditLogAdapter,
)
from src.domain.services.adapters.worker_index_sync_adapter import (
    WorkerIndexSyncAdapter,
)
from src.domain.services.adapters.worker_profile_filter_adapter import (
    WorkerProfileFilterAdapter,
)

# Phase D: Evidence Adapters
from src.domain.services.adapters.evidence_adapters import (
    # G1 Adapters
    scoring_signal_to_evidence,
    scoring_signals_to_evidences,
    G1_SIGNAL_TYPE_MAP,
    # G2 Adapters
    stance_signal_to_evidence,
    stance_signals_to_evidences,
    create_conflict_evidence,
    # G5 Adapters
    risk_factor_to_evidence,
    risk_factors_to_evidences,
    expert_evidence_to_evidence,
    scenario_prior_to_evidence,
    RISK_LEVEL_MAP,
    # Registry
    EvidenceAdapterRegistry,
    get_adapter_registry,
)


__all__ = [
    # Registry Adapters
    "WorkerRegistryStoreAdapter",
    "WorkerRuntimeStateStoreAdapter",
    "WorkerProfileBindingStoreAdapter",
    "WorkerAuditLogAdapter",
    "WorkerIndexSyncAdapter",
    "WorkerProfileFilterAdapter",
    # Phase D: Evidence Adapters
    "scoring_signal_to_evidence",
    "scoring_signals_to_evidences",
    "G1_SIGNAL_TYPE_MAP",
    "stance_signal_to_evidence",
    "stance_signals_to_evidences",
    "create_conflict_evidence",
    "risk_factor_to_evidence",
    "risk_factors_to_evidences",
    "expert_evidence_to_evidence",
    "scenario_prior_to_evidence",
    "RISK_LEVEL_MAP",
    "EvidenceAdapterRegistry",
    "get_adapter_registry",
]