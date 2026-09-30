"""
Application Services

应用服务，编排用例。

已实现的服务：
- WorkerRegistryService: Worker 注册服务（M1）
- WorkerProfilingService: Worker 档案抽取服务（M2）
- TaskUnderstandingService: 任务理解服务（M3）
- PlanningService: 规划服务（M4）
- RetrievalService: 检索服务（M5）
- TeamCompositionService: 团队组合服务（M6）
- WorkspaceAssemblyService: 工作空间组装服务（M7）
- ExecutionPacketService: 执行包编译服务（M8）

Stage 3: Worker Profile-Driven Expert Execution Preparation：
- G5ExpertEnhancerImpl: G5 专家视角增强实现

Stage 4: G5 real-context deepening / candidate recommendation：
- WorkerCandidateRecommendationImpl: 候选人推荐服务实现
  - 基于 question 推荐专家候选人
  - 支持 participants 充足性检查
  - 显式 participants 优先，补充推荐标记 is_supplement=True
"""

from src.application.services.worker_registry_service import WorkerRegistryService
from src.application.services.worker_profiling_service import WorkerProfilingService
from src.application.services.task_understanding_service import TaskUnderstandingService
from src.application.services.planning_service import PlanningService
from src.application.services.retrieval_service import RetrievalService
from src.application.services.team_composition_service import TeamCompositionService
from src.application.services.workspace_assembly_service import WorkspaceAssemblyService
from src.application.services.execution_packet_service import ExecutionPacketService

# Stage 3: Worker Profile-Driven Expert Execution Preparation
from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

# Stage 4: G5 real-context deepening / candidate recommendation 正式接入
from src.application.services.worker_candidate_recommendation_impl import (
    WorkerCandidateRecommendationImpl,
)

# Vector Store Adapter Baseline
from src.application.services.worker_vector_index_service import (
    WorkerVectorIndexService,
    IndexResult,
    SearchResult,
)

# Vector-Aware Worker Matching
from src.application.services.worker_vector_match_service import (
    WorkerVectorMatchService,
    MatchResult,
    RerankConfig,
)


__all__ = [
    "WorkerRegistryService",
    "WorkerProfilingService",
    "TaskUnderstandingService",
    "PlanningService",
    "RetrievalService",
    "TeamCompositionService",
    "WorkspaceAssemblyService",
    "ExecutionPacketService",
    # Stage 3
    "G5ExpertEnhancerImpl",
    # Stage 4
    "WorkerCandidateRecommendationImpl",
    # Vector Store Adapter Baseline
    "WorkerVectorIndexService",
    "IndexResult",
    "SearchResult",
    # Vector-Aware Worker Matching
    "WorkerVectorMatchService",
    "MatchResult",
    "RerankConfig",
]