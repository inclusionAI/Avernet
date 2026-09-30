"""
OPENCORE G2 Model-Service Contract Tests

Tests for OPENCORE-G2: Model/Service contract validation.

This test module verifies:
1. Worker request/response models import and instantiate
2. Profile request/response models import and instantiate
3. Search request/response models import and instantiate
4. Recommend request/response models import and instantiate
5. Fusion request/response models import and instantiate
6. Verify request/response models import and instantiate
7. Core services import without internal provider imports
8. Core service constructors accept public-safe dependencies
9. No service import requires bcsfuse_internal
10. No service import requires ZDAS/MIST/Sofapy/DRM/BCN/OceanBase
11. Invalid model payloads fail with expected validation errors
12. Valid minimal payloads pass validation

No database, LLM, embedding, MySQL, internal runtime, or external services required.
"""

import pytest
from pydantic import ValidationError


class TestWorkerModelsContract:
    """Test Worker domain models contract."""

    def test_worker_model_imports(self):
        """Test Worker model can be imported."""
        from src.domain.models.worker import (
            Worker,
            WorkerType,
            WorkerIdentity,
        )
        assert Worker is not None
        assert WorkerType is not None
        assert WorkerIdentity is not None

    def test_worker_model_minimal_instantiation(self):
        """Test minimal Worker model instantiation."""
        from src.domain.models.worker import Worker, WorkerType

        worker_data = {
            "id": "wrk_test_001",
            "type": WorkerType.BOT,
            "identity": {
                "name": "Test Bot",
                "handle": "test_bot",
                "description": "Test Description",
            },
            "responsibilities": [],
            "capabilities": [],
            "state": {
                "availability": "public",
                "trust_level": "unverified",
            },
        }
        worker = Worker.model_validate(worker_data)
        assert worker.id == "wrk_test_001"
        assert worker.type == WorkerType.BOT

    def test_worker_profile_model_imports(self):
        """Test WorkerProfile model can be imported."""
        from src.domain.models.worker_profile import WorkerProfile
        assert WorkerProfile is not None

    def test_worker_profile_binding_model_imports(self):
        """Test WorkerProfileBinding model can be imported."""
        from src.domain.models.worker_profile_binding import WorkerProfileBinding
        assert WorkerProfileBinding is not None

    def test_profile_fragment_model_imports(self):
        """Test ProfileFragment model can be imported."""
        from src.domain.models.profile_fragment import ProfileFragment
        assert ProfileFragment is not None

    def test_profile_fragment_minimal_instantiation(self):
        """Test minimal ProfileFragment instantiation."""
        from src.domain.models.profile_fragment import ProfileFragment

        fragment = ProfileFragment(
            fragment_type="skills",
            content="Python programming",
        )
        assert fragment.fragment_type == "skills"
        assert fragment.content == "Python programming"
        assert fragment.weight == 1.0

    def test_worker_invalid_payload_fails(self):
        """Test invalid Worker payload fails validation."""
        from src.domain.models.worker import Worker

        with pytest.raises(ValidationError):
            Worker.model_validate({})  # missing required fields


class TestProfileModelsContract:
    """Test Profile domain models contract."""

    def test_skill_profile_model_imports(self):
        """Test SkillProfile model can be imported."""
        from src.domain.models.skill_profile import SkillProfile
        assert SkillProfile is not None

    def test_profiling_input_model_imports(self):
        """Test ProfilingInput model can be imported."""
        from src.domain.models.profiling_input import ProfilingInput
        assert ProfilingInput is not None

    def test_profiling_result_model_imports(self):
        """Test WorkerProfileExtractionResult model can be imported."""
        from src.domain.models.profiling_result import WorkerProfileExtractionResult
        assert WorkerProfileExtractionResult is not None


class TestSearchModelsContract:
    """Test Search related models contract."""

    def test_profile_match_score_model_imports(self):
        """Test ProfileMatchScore model can be imported."""
        from src.domain.models.profile_match_score import ProfileMatchScore
        assert ProfileMatchScore is not None

    def test_hybrid_score_model_imports(self):
        """Test HybridScore model can be imported."""
        from src.domain.models.hybrid_score import HybridScore
        assert HybridScore is not None

    def test_retrieval_mode_model_imports(self):
        """Test RetrievalMode model can be imported."""
        from src.domain.models.retrieval_mode import RetrievalMode
        assert RetrievalMode is not None


class TestRecommendModelsContract:
    """Test Recommend request/response models contract."""

    def test_recommend_schemas_import(self):
        """Test recommend schemas import."""
        from src.interfaces.api.schemas.recommend_schemas import (
            BotRecommendationRequest,
            BotRecommendation,
            BotRecommendationResponse,
        )
        assert BotRecommendationRequest is not None
        assert BotRecommendation is not None
        assert BotRecommendationResponse is not None

    def test_recommend_request_minimal_instantiation(self):
        """Test minimal BotRecommendationRequest instantiation."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        req = BotRecommendationRequest(question="test question")
        assert req.question == "test question"
        assert req.topK == 5
        assert req.min_score == 0.01

    def test_recommend_request_invalid_payload_fails(self):
        """Test invalid BotRecommendationRequest fails validation."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="")  # empty question

        with pytest.raises(ValidationError):
            BotRecommendationRequest()  # missing question


class TestFusionModelsContract:
    """Test Fusion request/response models contract."""

    def test_fusion_schemas_import(self):
        """Test fusion schemas import."""
        from src.interfaces.api.schemas.fusion_schemas import (
            FuseOptions,
            FusionRequest,
            FusionResult,
            FusionPerspective,
            ConflictPoint,
            AlignmentPoint,
            RiskAssessment,
        )
        assert FuseOptions is not None
        assert FusionRequest is not None
        assert FusionResult is not None

    def test_fusion_request_minimal_instantiation(self):
        """Test minimal FusionRequest instantiation."""
        from src.interfaces.api.schemas.fusion_schemas import FusionRequest

        req = FusionRequest(question="test question", participants=["bot1"])
        assert req.question == "test question"
        assert req.participants == ["bot1"]
        assert req.fusion_mode == "agent"

    def test_fusion_options_minimal_instantiation(self):
        """Test minimal FuseOptions instantiation."""
        from src.interfaces.api.schemas.fusion_schemas import FuseOptions

        opts = FuseOptions()
        assert opts.timeout_ms == 120000
        assert opts.parallel is True

    def test_fusion_request_invalid_payload_fails(self):
        """Test invalid FusionRequest fails validation."""
        from src.interfaces.api.schemas.fusion_schemas import FusionRequest

        with pytest.raises(ValidationError):
            FusionRequest(question="test", participants=[])


class TestVerifyModelsContract:
    """Test Verify request/response models contract."""

    def test_verify_schemas_import(self):
        """Test verify schemas import."""
        from src.interfaces.api.schemas.verify_schemas import (
            BatchVerifyRequest,
            BatchVerifyAllRequest,
            DimensionResult,
            DimensionJudgment,
            CapabilityVerificationResult,
            WorkerVerifyResult,
            BatchVerifyResponse,
        )
        assert BatchVerifyRequest is not None
        assert BatchVerifyAllRequest is not None
        assert BatchVerifyResponse is not None

    def test_batch_verify_request_minimal_instantiation(self):
        """Test minimal BatchVerifyRequest instantiation."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyRequest

        req = BatchVerifyRequest(worker_ids=["wrk_001"])
        assert req.worker_ids == ["wrk_001"]
        assert req.capabilities is None

    def test_batch_verify_all_request_minimal_instantiation(self):
        """Test minimal BatchVerifyAllRequest instantiation."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyAllRequest

        req = BatchVerifyAllRequest()
        assert req.capabilities is None
        assert req.filters == {}

    def test_verify_request_invalid_payload_fails(self):
        """Test invalid BatchVerifyRequest fails validation."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyRequest

        with pytest.raises(ValidationError):
            BatchVerifyRequest(worker_ids=[])


class TestServicesContract:
    """Test Core Services contract."""

    def test_worker_registry_service_imports(self):
        """Test WorkerRegistryService can be imported."""
        from src.application.services.worker_registry_service import WorkerRegistryService
        assert WorkerRegistryService is not None

    def test_worker_profiling_service_imports(self):
        """Test WorkerProfilingService can be imported."""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        assert WorkerProfilingService is not None

    def test_worker_profile_query_service_imports(self):
        """Test WorkerProfileQueryService can be imported."""
        from src.application.services.worker_profile_query_service import WorkerProfileQueryService
        assert WorkerProfileQueryService is not None

    def test_semantic_match_service_imports(self):
        """Test SemanticMatchService can be imported."""
        from src.application.services.semantic_match_service import SemanticMatchService
        assert SemanticMatchService is not None

    def test_group_fusion_service_imports(self):
        """Test GroupFusionService can be imported."""
        from src.application.services.group_fusion_service import GroupFusionService
        assert GroupFusionService is not None

    def test_verify_executor_imports(self):
        """Test VerifyExecutor can be imported."""
        from src.application.services.verify_executor import VerifyExecutor
        assert VerifyExecutor is not None

    def test_verify_judge_imports(self):
        """Test VerifyJudge can be imported."""
        from src.application.services.verify_judge import VerifyJudge
        assert VerifyJudge is not None

    def test_planning_service_imports(self):
        """Test PlanningService can be imported."""
        from src.application.services.planning_service import PlanningService
        assert PlanningService is not None

    def test_worker_vector_index_service_imports(self):
        """Test WorkerVectorIndexService can be imported."""
        from src.application.services.worker_vector_index_service import WorkerVectorIndexService
        assert WorkerVectorIndexService is not None


class TestServiceConstructorContract:
    """Test that services accept public-safe dependencies."""

    def test_worker_registry_service_constructor(self):
        """Test WorkerRegistryService constructor signature."""
        from src.application.services.worker_registry_service import WorkerRegistryService
        from src.domain.services.worker_repository import WorkerRepository
        import inspect

        sig = inspect.signature(WorkerRegistryService.__init__)
        params = list(sig.parameters.keys())

        # Should have 'self' and 'repository' parameters
        assert 'self' in params
        assert 'repository' in params

        # Repository parameter should be present (annotation may be string or class)
        param_annotation = sig.parameters['repository'].annotation
        # Accept both string annotation 'WorkerRepository' and actual class
        assert param_annotation == WorkerRepository or param_annotation == 'WorkerRepository'

    def test_worker_profiling_service_constructor(self):
        """Test WorkerProfilingService constructor signature."""
        from src.application.services.worker_profiling_service import WorkerProfilingService
        import inspect

        sig = inspect.signature(WorkerProfilingService.__init__)
        params = list(sig.parameters.keys())

        # Should have 'self' and dependencies
        assert 'self' in params
        # Should have some dependency parameters (varies by service)
        assert len(params) > 1


class TestNoInternalDependencies:
    """Test that models and services don't import internal dependencies."""

    def test_no_bcsfuse_internal_imports_in_models(self):
        """Test that domain models don't import bcsfuse_internal."""
        import sys

        # Import models
        from src.domain.models import worker, profile_fragment, worker_profile

        # Check that forbidden modules are not loaded
        forbidden_modules = [
            'bcsfuse_internal',
            'zdas',
            'mist',
            'sofapy',
            'drm',
            'bcn',
            'oceanbase',
            'mosn',
            'layotto',
        ]

        for module_name in forbidden_modules:
            assert module_name not in sys.modules, f"Forbidden module {module_name} is loaded"

    def test_no_internal_infra_imports_in_services(self):
        """Test that services don't import internal infrastructure."""
        import sys

        # Import services
        from src.application.services import worker_registry_service

        # Check that forbidden modules are not loaded
        forbidden_modules = [
            'bcsfuse_internal',
            'zdas',
            'mist',
            'sofapy',
            'drm',
            'bcn',
            'oceanbase',
            'mosn',
            'layotto',
            'ant_sofapy_base',
        ]

        for module_name in forbidden_modules:
            assert module_name not in sys.modules, f"Forbidden module {module_name} is loaded"

    def test_schemas_no_internal_dependencies(self):
        """Test that API schemas don't import internal dependencies."""
        import sys

        # Import all schemas
        from src.interfaces.api.schemas import (
            recommend_schemas,
            fusion_schemas,
            verify_schemas,
        )

        # Check forbidden modules
        forbidden_modules = [
            'bcsfuse_internal',
            'zdas',
            'mist',
            'sofapy',
            'drm',
            'bcn',
            'oceanbase',
        ]

        for module_name in forbidden_modules:
            assert module_name not in sys.modules, f"Forbidden module {module_name} is loaded in schemas"


class TestModelValidationContract:
    """Test model validation behavior."""

    def test_worker_validation_errors(self):
        """Test Worker model validation errors."""
        from src.domain.models.worker import Worker

        # Missing id
        with pytest.raises(ValidationError) as exc_info:
            Worker.model_validate({"type": "bot"})
        assert "id" in str(exc_info.value).lower()

    def test_profile_fragment_validation(self):
        """Test ProfileFragment validation."""
        from src.domain.models.profile_fragment import ProfileFragment

        # Valid fragment
        fragment = ProfileFragment(
            fragment_type="test",
            content="test content",
        )
        assert fragment.content == "test content"

        # Empty content is allowed (defaults to "")
        fragment_empty = ProfileFragment(
            fragment_type="test",
            content=None,  # Will be converted to ""
        )
        assert fragment_empty.content == ""

    def test_recommend_request_validation_errors(self):
        """Test BotRecommendationRequest validation errors."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        # topK out of range
        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", topK=0)

        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", topK=100)

        # min_score out of range
        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", min_score=-0.1)

        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", min_score=1.1)


class TestAdditionalDomainModels:
    """Test additional domain models contract."""

    def test_fusion_result_model_imports(self):
        """Test FusionResult model can be imported."""
        from src.domain.models.fusion_result import FusionResult
        assert FusionResult is not None

    def test_fusion_recommendation_model_imports(self):
        """Test FusionRecommendation model can be imported."""
        from src.domain.models.fusion_recommendation import FusionRecommendation
        assert FusionRecommendation is not None

    def test_expert_context_pack_model_imports(self):
        """Test ExpertContextPack model can be imported."""
        from src.domain.models.expert_context_pack import ExpertContextPack
        assert ExpertContextPack is not None

    def test_execution_packet_model_imports(self):
        """Test ExecutionPacket model can be imported."""
        from src.domain.models.execution_packet import ExecutionPacket
        assert ExecutionPacket is not None

    def test_evidence_bundle_model_imports(self):
        """Test EvidenceBundle model can be imported."""
        from src.domain.models.evidence_bundle import EvidenceBundle
        assert EvidenceBundle is not None

    def test_handoff_bundle_model_imports(self):
        """Test HandoffBundle model can be imported."""
        from src.domain.models.handoff_bundle import HandoffBundle
        assert HandoffBundle is not None

    def test_metadata_record_model_imports(self):
        """Test MetadataRecord model can be imported."""
        from src.domain.models.metadata_record import MetadataRecord
        assert MetadataRecord is not None

    def test_structured_risk_assessment_model_imports(self):
        """Test StructuredRiskAssessment model can be imported."""
        from src.domain.models.structured_risk_assessment import StructuredRiskAssessment
        assert StructuredRiskAssessment is not None


class TestWorkerConfigModels:
    """Test Worker Config models contract."""

    def test_worker_config_model_imports(self):
        """Test WorkerConfig model can be imported."""
        from src.domain.models.worker_config import WorkerConfig
        assert WorkerConfig is not None

    def test_worker_lifecycle_state_model_imports(self):
        """Test WorkerLifecycleState model can be imported."""
        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
        assert WorkerLifecycleState is not None

    def test_worker_runtime_state_model_imports(self):
        """Test WorkerRuntimeState model can be imported."""
        from src.domain.models.worker_runtime_state import WorkerRuntimeState
        assert WorkerRuntimeState is not None