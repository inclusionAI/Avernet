"""
Domain Services

领域服务接口定义。

已定义的接口：
- WorkerRepository: Worker 仓库接口（M1）
- TaskUnderstander: 任务理解器接口（M3）
- WorkerProfileSource: Worker Profile 来源接口

新增服务（Worker Profile Retrieval & Fusion Simulation）：
- WorkerProfileRetrievalService: Fuse 检索契约
- WorkerContextPreparationService: Context 裁剪服务
- FusionSimulationService: G1/G2/G5 融合模拟服务

Stage 3: Worker Profile-Driven Expert Execution Preparation：
- G5ExpertEnhancer: G5 专家视角增强接口
"""

# Phase D: Unified Evidence Layer
from app.domain.services.evidence_aggregation_service import (
    AggregationConfig,
    EvidenceAggregationService,
    get_evidence_aggregation_service,
    reset_evidence_aggregation_service,
)
from app.domain.services.explanation_builder_v2 import (
    ExplanationBuilderV2,
    ExplanationStyle,
    get_explanation_builder,
    reset_explanation_builder,
)
from app.domain.services.fusion_simulation_service import (
    FusionSimulationService,
)

# Stage 3: Worker Profile-Driven Expert Execution Preparation
from app.domain.services.g5_expert_enhancer import G5ExpertEnhancer
from app.domain.services.metadata_store_adapter import MetadataStoreAdapter
from app.domain.services.participants_sufficiency_checker import (
    ParticipantsSufficiencyChecker,
    SufficiencyCheckResult,
)

# Reranker
from app.domain.services.reranker import Reranker, RerankResult
from app.domain.services.task_understander import TaskUnderstander

# Vector Store Adapter Baseline
from app.domain.services.vector_store_adapter import VectorStoreAdapter

# Stage 4: G5 real-context deepening / candidate recommendation 正式接入
from app.domain.services.worker_candidate_recommendation_service import (
    WorkerCandidateRecommendationService,
)
from app.domain.services.worker_context_preparation_service import (
    WorkerContextPreparationService,
)
from app.domain.services.worker_profile_retrieval_service import (
    RetrievalResponse,
    RetrievalResult,
    WorkerProfileRetrievalService,
)
from app.domain.services.worker_profile_source import WorkerProfileSource
from app.domain.services.worker_repository import WorkerRepository

__all__ = [
    "WorkerRepository",
    "TaskUnderstander",
    "WorkerProfileSource",
    "WorkerProfileRetrievalService",
    "RetrievalResult",
    "RetrievalResponse",
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