"""
API Dependencies

FastAPI 依赖注入模块。

Stage 1 Phase 4.5: 添加 Fusion 相关依赖注入，支持 Registry-aware filtering。
"""

from src.interfaces.api.dependencies.worker_dependencies import (
    get_worker_import_service,
    get_worker_runtime_state_service,
    get_registry_store,
    reset_stores,
    use_in_memory_stores,
)
from src.interfaces.api.dependencies.fusion_dependencies import (
    get_registry_aware_filter,
    get_profile_retrieval_service,
    get_candidate_recommendation_service,
    get_expert_diagnosis_service,
    get_group_fusion_service,
    configure_filter,
    set_profile_source,
    reset_fusion_services,
)

__all__ = [
    # Worker Dependencies
    "get_worker_import_service",
    "get_worker_runtime_state_service",
    "get_registry_store",
    "reset_stores",
    "use_in_memory_stores",
    # Fusion Dependencies (Stage 1 Phase 4.5)
    "get_registry_aware_filter",
    "get_profile_retrieval_service",
    "get_candidate_recommendation_service",
    "get_expert_diagnosis_service",
    "get_group_fusion_service",
    "configure_filter",
    "set_profile_source",
    "reset_fusion_services",
]