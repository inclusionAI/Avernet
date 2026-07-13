"""
API Schemas Module

Public-safe request/response models for BCSFuse API routes.

S28B-2B-12: Public-safe contract models for route skeletons.
"""

from src.interfaces.api.schemas.recommend_schemas import (
    BotRecommendation,
    BotRecommendationRequest,
    BotRecommendationResponse,
)
from src.interfaces.api.schemas.fusion_schemas import (
    AlignmentPoint,
    ConflictPoint,
    FuseMetadata,
    FuseOptions,
    FusionPerspective,
    FusionRequest,
    FusionResult,
    RiskAssessment,
)
from src.interfaces.api.schemas.verify_schemas import (
    BatchVerifyAllRequest,
    BatchVerifyRequest,
    BatchVerifyResponse,
    CapabilityVerificationResult,
    DimensionJudgment,
    DimensionResult,
    WorkerVerifyResult,
)
from src.interfaces.api.schemas.worker_management_schemas import (
    Availability,
    TrustLevel,
    WorkerAvailabilityResponse,
    WorkerAvailabilityUpdate,
    WorkerBatchQueryRequest,
    WorkerBatchQueryResponse,
    WorkerConfigBatchResponse,
    WorkerConfigBatchUpdate,
    WorkerConfigResponse,
    WorkerConfigUpdate,
    WorkerPatchRequest,
    WorkerPatchResponse,
    WorkerProfileQualityResponse,
    WorkerSyncRequest,
    WorkerSyncResponse,
    WorkerTrustLevelResponse,
    WorkerTrustLevelUpdate,
    WorkersBySourceResponse,
)
from src.interfaces.api.schemas.profile_management_schemas import (
    ActiveProfileItem,
    ActiveProfilesResponse,
    ProfileActivateResponse,
    ProfileAnalyzeRequest,
    ProfileAnalyzeResponse,
    ProfileCapabilityAnalysis,
    ProfilePatchRequest,
    ProfilePatchResponse,
    ProfileQualityResponse,
    ProfileQualityScore,
    ProfileSearchRequest,
    ProfileSearchResponse,
    ProfileSearchResult,
)
from src.interfaces.api.schemas.worker_config_schemas import (
    BatchQueryConfigRequest,
    BatchQueryConfigResponse,
    SetWorkerConfigRequest,
    WorkerConfigItem,
    WorkerConfigResponse as LegacyWorkerConfigResponse,
)

__all__ = [
    # Recommend schemas
    "BotRecommendationRequest",
    "BotRecommendation",
    "BotRecommendationResponse",
    # Fusion schemas
    "FuseOptions",
    "FuseMetadata",
    "FusionRequest",
    "FusionPerspective",
    "ConflictPoint",
    "AlignmentPoint",
    "RiskAssessment",
    "FusionResult",
    # Verify schemas
    "BatchVerifyRequest",
    "BatchVerifyAllRequest",
    "DimensionResult",
    "DimensionJudgment",
    "CapabilityVerificationResult",
    "WorkerVerifyResult",
    "BatchVerifyResponse",
    # Worker management schemas
    "Availability",
    "TrustLevel",
    "WorkerBatchQueryRequest",
    "WorkerBatchQueryResponse",
    "WorkerSyncRequest",
    "WorkerSyncResponse",
    "WorkerAvailabilityUpdate",
    "WorkerAvailabilityResponse",
    "WorkerTrustLevelUpdate",
    "WorkerTrustLevelResponse",
    "WorkerPatchRequest",
    "WorkerPatchResponse",
    "WorkerConfigResponse",
    "WorkerConfigUpdate",
    "WorkerConfigBatchUpdate",
    "WorkerConfigBatchResponse",
    "WorkersBySourceResponse",
    "WorkerProfileQualityResponse",
    # Worker config schemas (legacy)
    "SetWorkerConfigRequest",
    "WorkerConfigItem",
    "BatchQueryConfigRequest",
    "BatchQueryConfigResponse",
    "LegacyWorkerConfigResponse",
    # Profile management schemas
    "ProfileSearchRequest",
    "ProfileSearchResult",
    "ProfileSearchResponse",
    "ActiveProfileItem",
    "ActiveProfilesResponse",
    "ProfileQualityScore",
    "ProfileQualityResponse",
    "ProfileAnalyzeRequest",
    "ProfileCapabilityAnalysis",
    "ProfileAnalyzeResponse",
    "ProfilePatchRequest",
    "ProfilePatchResponse",
    "ProfileActivateResponse",
]