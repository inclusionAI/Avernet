"""
Domain Services

领域服务接口定义。

已定义的接口：
- WorkerRepository: Worker 仓库接口（M1）
- TaskUnderstander: 任务理解器接口（M3）
- WorkerProfileSource: Worker Profile 来源接口

新增服务（Worker Profile Retrieval & Fusion Simulation）：
- WorkerProfileRetrievalService: Mode-aware 检索服务
- WorkerContextPreparationService: Context 裁剪服务
- FusionSimulationService: G1/G2/G5 融合模拟服务

Stage 3: Worker Profile-Driven Expert Execution Preparation：
- G5ExpertEnhancer: G5 专家视角增强接口
"""

from src.domain.services.worker_repository import WorkerRepository
from src.domain.services.task_understander import TaskUnderstander
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.domain.services.worker_profile_retrieval_service import (
    WorkerProfileRetrievalService,
    RetrievalResult,
    RetrievalResponse,
    ModeAwareScorer,
)
from src.domain.services.worker_context_preparation_service import (
    WorkerContextPreparationService,
)
from src.domain.services.fusion_simulation_service import (
    FusionSimulationService,
)

# Stage 3: Worker Profile-Driven Expert Execution Preparation
from src.domain.services.g5_expert_enhancer import G5ExpertEnhancer

# Stage 4: G5 real-context deepening / candidate recommendation 正式接入
from src.domain.services.worker_candidate_recommendation_service import (
    WorkerCandidateRecommendationService,
)
from src.domain.services.participants_sufficiency_checker import (
    ParticipantsSufficiencyChecker,
    SufficiencyCheckResult,
)

# Vector Store Adapter Baseline
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.domain.services.metadata_store_adapter import MetadataStoreAdapter

# Phase D: Unified Evidence Layer
from src.domain.services.evidence_aggregation_service import (
    AggregationConfig,
    EvidenceAggregationService,
    get_evidence_aggregation_service,
    reset_evidence_aggregation_service,
)
from src.domain.services.explanation_builder_v2 import (
    ExplanationStyle,
    ExplanationBuilderV2,
    get_explanation_builder,
    reset_explanation_builder,
)

# Reranker
from src.domain.services.reranker import Reranker, RerankResult


__all__ = [
    "WorkerRepository",
    "TaskUnderstander",
    "WorkerProfileSource",
    "WorkerProfileRetrievalService",
    "RetrievalResult",
    "RetrievalResponse",
    "ModeAwareScorer",
    "WorkerContextPreparationService",
    "FusionSimulationService",
    # Stage 3
    "G5ExpertEnhancer",
    # Stage 4
    "WorkerCandidateRecommendationService",
    "ParticipantsSufficiencyChecker",
    "SufficiencyCheckResult",
    # Vector Store Adapter Baseline
    "VectorStoreAdapter",
    "MetadataStoreAdapter",
    # Phase D: Unified Evidence Layer
    "AggregationConfig",
    "EvidenceAggregationService",
    "get_evidence_aggregation_service",
    "reset_evidence_aggregation_service",
    "ExplanationStyle",
    "ExplanationBuilderV2",
    "get_explanation_builder",
    "reset_explanation_builder",
    # Reranker
    "Reranker",
    "RerankResult",
]