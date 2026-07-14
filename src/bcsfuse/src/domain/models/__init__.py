"""
Domain Models

核心领域对象定义，与 JSON Schema 对齐。

已实现的模型：
- Worker: Worker 主档案（M1 完成）
- TaskSpec: 任务规格（M3 完成）
- PlanDraft: 计划草案（M4 完成）
- CandidateBundle: 候选集（M5 完成）
- TeamSpec: 团队规格（M6 完成）
- Workspace: 工作空间（M7 完成）
- ExecutionPacket: 执行包（M8 完成）
- CompilerInput: 编译器输入（M8 完成）
- CompilerResult: 编译器结果（M8 完成）

Worker Profile Retrieval & Fusion Simulation：
- WorkerProfile: Worker Profile 聚合模型
- ContextFragment: 上下文片段模型
- SkillProfile: 技能档案模型
- RetrievalMode: 检索模式枚举
- ScoringSignal: 打分信号模型
- WorkerContextDigest: Task-specific 上下文摘要
- FusionSimulationInput: Fusion Simulation 输入模型

Stage 3: Worker Profile-Driven Expert Execution Preparation：
- ExpertContextPack: G5 LLM 输入上下文
- LLMExpertPerspective: LLM 生成的专家视角
"""

from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    Capability,
    CapabilityLevel,
    Constraint,
    ConstraintKind,
    ConstraintSeverity,
    SkillRef,
    SkillSource,
    ResourceRef,
    ResourceKind,
    ResourceAccess,
    WorkerState,
    Availability,
    TrustLevel,
    PerformanceStats,
    WorkerConfig,
)

from src.domain.models.task_spec import (
    TaskSpec,
    RiskLevel,
    Subtask,
)

from src.domain.models.task_understanding_input import TaskUnderstandingInput

from src.domain.models.task_understanding_result import (
    TaskUnderstandingResult,
    UnderstandingWarning,
    UnderstandingError,
)

from src.domain.models.plan_draft import (
    PlanDraft,
    PlanStep,
)

from src.domain.models.candidate_bundle import (
    CandidateBundle,
    KnowledgeItem,
)

from src.domain.models.team_spec import (
    TeamSpec,
    RoleAssignment,
)

from src.domain.models.workspace import (
    Workspace,
    WorkspaceStatus,
    WorkspaceEvent,
)

from src.domain.models.execution_packet import (
    ExecutionPacket,
    ContextPack,
    ResourcePack,
    SkillPack,
    Guardrails,
    OutputContract,
)

from src.domain.models.compiler_input import (
    CompilerInput,
    CompilerHints,
)

from src.domain.models.compiler_result import (
    CompilerResult,
    CompilerExplanation,
    CompilerWarning,
    CompilerError,
)

# Worker Profile Retrieval & Fusion Simulation
from src.domain.models.worker_profile import (
    WorkerProfile,
    ProfileType,
    ProfileMatchResult,
    ProfileSearchResult,
    ProfileRecommendResult,
    WorkerProfileScanResult,
)

from src.domain.models.context_fragment import (
    ContextFragment,
    ContextKind,
)

from src.domain.models.skill_profile import SkillProfile

from src.domain.models.retrieval_mode import (
    RetrievalMode,
    FusionModeLiteral,
)

from src.domain.models.scoring_signal import (
    ScoringSignal,
    SignalType,
)

from src.domain.models.worker_context_digest import WorkerContextDigest

from src.domain.models.fusion_simulation_input import FusionSimulationInput

# Stage 3: Worker Profile-Driven Expert Execution Preparation
from src.domain.models.expert_context_pack import ExpertContextPack
from src.domain.models.llm_expert_perspective import (
    LLMExpertPerspective,
    RiskLevelLiteral,
)

# Stage 4: G5 real-context deepening / candidate recommendation 正式接入
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)

# Vector Store Adapter Baseline
from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.models.metadata_record import MetadataRecord

# G5 V2: Structured Risk Assessment
from src.domain.models.structured_risk_assessment import (
    RiskFactor,
    BlockingCondition,
    ExpertEvidence,
    ScenarioPriorRisk,
    StructuredRiskAssessment,
)

# Phase D: Unified Evidence Layer
from src.domain.models.evidence import (
    EvidenceType,
    EvidenceSource,
    Evidence,
    EvidenceProvenance,
    EvidenceSourceDistribution,
)
from src.domain.models.evidence_bundle import (
    EvidenceContribution,
    EvidenceBundle,
)
from src.domain.models.fallback_reason_v2 import (
    FallbackReasonCode,
    FallbackChain,
    FallbackReasonV2,
)

# Bot Recommendation API
from src.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    BotRecommendation,
    BotRecommendationResponse,
    create_bot_recommendation_response,
)

# G9: Profile Fusion
from src.domain.models.profile_fusion import (
    FusedProfile,
    ExpertProfile,
    GroupConversationSummary,
)
from src.domain.models.profile_fusion.fused_profile import ProfileFusionResult

# G9: Fusion Storage
from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn, ConversationStats
from src.domain.models.profile_fusion import FusionContext

__all__ = [
    # Worker
    "Worker",
    "WorkerType",
    "WorkerIdentity",
    "Capability",
    "CapabilityLevel",
    "Constraint",
    "ConstraintKind",
    "ConstraintSeverity",
    "SkillRef",
    "SkillSource",
    "ResourceRef",
    "ResourceKind",
    "ResourceAccess",
    "WorkerState",
    "Availability",
    "TrustLevel",
    "PerformanceStats",
    "WorkerConfig",
    # TaskSpec
    "TaskSpec",
    "RiskLevel",
    "Subtask",
    # TaskUnderstandingInput
    "TaskUnderstandingInput",
    # TaskUnderstandingResult
    "TaskUnderstandingResult",
    "UnderstandingWarning",
    "UnderstandingError",
    # PlanDraft
    "PlanDraft",
    "PlanStep",
    # CandidateBundle
    "CandidateBundle",
    "KnowledgeItem",
    # TeamSpec
    "TeamSpec",
    "RoleAssignment",
    # Workspace
    "Workspace",
    "WorkspaceStatus",
    "WorkspaceEvent",
    # ExecutionPacket
    "ExecutionPacket",
    "ContextPack",
    "ResourcePack",
    "SkillPack",
    "Guardrails",
    "OutputContract",
    # CompilerInput
    "CompilerInput",
    "CompilerHints",
    # CompilerResult
    "CompilerResult",
    "CompilerExplanation",
    "CompilerWarning",
    "CompilerError",
    # Worker Profile Retrieval & Fusion Simulation
    "WorkerProfile",
    "ProfileType",
    "ProfileMatchResult",
    "ProfileSearchResult",
    "ProfileRecommendResult",
    "WorkerProfileScanResult",
    "ContextFragment",
    "ContextKind",
    "SkillProfile",
    "RetrievalMode",
    "FusionModeLiteral",
    "ScoringSignal",
    "SignalType",
    "WorkerContextDigest",
    "FusionSimulationInput",
    # Stage 3: Worker Profile-Driven Expert Execution Preparation
    "ExpertContextPack",
    "LLMExpertPerspective",
    "RiskLevelLiteral",
    # Stage 4: G5 real-context deepening / candidate recommendation 正式接入
    "DomainCoverage",
    "CandidateRecommendation",
    "CandidateRecommendationResponse",
    # Vector Store Adapter Baseline
    "VectorPoint",
    "VectorSearchHit",
    "MetadataRecord",
    # G5 V2: Structured Risk Assessment
    "RiskFactor",
    "BlockingCondition",
    "ExpertEvidence",
    "ScenarioPriorRisk",
    "StructuredRiskAssessment",
    # Phase D: Unified Evidence Layer
    "EvidenceType",
    "EvidenceSource",
    "Evidence",
    "EvidenceProvenance",
    "EvidenceSourceDistribution",
    "EvidenceContribution",
    "EvidenceBundle",
    "FallbackReasonCode",
    "FallbackChain",
    "FallbackReasonV2",
    # Bot Recommendation API
    "BotRecommendationRequest",
    "BotRecommendation",
    "BotRecommendationResponse",
    "create_bot_recommendation_response",
    # G9: Profile Fusion
    "FusedProfile",
    "ExpertProfile",
    "ProfileFusionResult",
    "GroupConversationSummary",
    # G9: Fusion Storage
    "FusedProfileRecord",
    "ConversationTurn",
    "ConversationStats",
    # G9: Fusion Context
    "FusionContext",
]