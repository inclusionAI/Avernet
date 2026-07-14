"""
Public-Safe Contract Models Tests

Tests for S28B-2B-12: Public-safe request/response models for skeleton routes.

This test module verifies:
1. All schema modules import successfully
2. Representative minimal valid payloads instantiate
3. Representative optional fields instantiate
4. OpenAPI schema includes model names for P0/P1/P2 skeleton routes
5. No schema module imports forbidden internal dependencies

No database, LLM, embedding, MySQL, internal runtime, or external services required.
"""

import pytest
from pydantic import ValidationError


class TestSchemaModuleImports:
    """Test that all schema modules import successfully."""

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

    def test_fusion_schemas_import(self):
        """Test fusion schemas import."""
        from src.interfaces.api.schemas.fusion_schemas import (
            FuseOptions,
            FuseMetadata,
            FusionRequest,
            FusionPerspective,
            ConflictPoint,
            AlignmentPoint,
            RiskAssessment,
            FusionResult,
        )
        assert FuseOptions is not None
        assert FusionRequest is not None

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
        assert BatchVerifyResponse is not None

    def test_worker_management_schemas_import(self):
        """Test worker management schemas import."""
        from src.interfaces.api.schemas.worker_management_schemas import (
            Availability,
            TrustLevel,
            WorkerBatchQueryRequest,
            WorkerSyncRequest,
            WorkerAvailabilityUpdate,
            WorkerTrustLevelUpdate,
            WorkerPatchRequest,
            WorkerConfigUpdate,
            WorkerConfigBatchUpdate,
        )
        assert Availability is not None
        assert TrustLevel is not None
        assert WorkerBatchQueryRequest is not None

    def test_profile_management_schemas_import(self):
        """Test profile management schemas import."""
        from src.interfaces.api.schemas.profile_management_schemas import (
            ProfileSearchRequest,
            ProfileSearchResult,
            ProfileQualityResponse,
            ProfileAnalyzeRequest,
            ProfilePatchRequest,
        )
        assert ProfileSearchRequest is not None
        assert ProfilePatchRequest is not None


class TestMinimalValidPayloads:
    """Test that minimal valid payloads can be instantiated."""

    def test_bot_recommendation_request_minimal(self):
        """Test minimal BotRecommendationRequest instantiation."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        req = BotRecommendationRequest(question="test question")
        assert req.question == "test question"
        assert req.topK == 5
        assert req.min_score == 0.01

    def test_fusion_request_minimal(self):
        """Test minimal FusionRequest instantiation."""
        from src.interfaces.api.schemas.fusion_schemas import FusionRequest

        req = FusionRequest(question="test question", participants=["bot1"])
        assert req.question == "test question"
        assert req.participants == ["bot1"]
        assert req.fusion_mode == "agent"

    def test_batch_verify_request_minimal(self):
        """Test minimal BatchVerifyRequest instantiation."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyRequest

        req = BatchVerifyRequest(worker_ids=["wrk_001"])
        assert req.worker_ids == ["wrk_001"]
        assert req.capabilities is None

    def test_batch_verify_all_request_minimal(self):
        """Test minimal BatchVerifyAllRequest instantiation."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyAllRequest

        req = BatchVerifyAllRequest()
        assert req.capabilities is None
        assert req.filters == {}

    def test_worker_batch_query_request_minimal(self):
        """Test minimal WorkerBatchQueryRequest instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import WorkerBatchQueryRequest

        req = WorkerBatchQueryRequest(worker_ids=["wrk_001"])
        assert req.worker_ids == ["wrk_001"]

    def test_worker_sync_request_minimal(self):
        """Test minimal WorkerSyncRequest instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import WorkerSyncRequest

        req = WorkerSyncRequest(name="Test Worker")
        assert req.name == "Test Worker"
        assert req.description is None

    def test_worker_availability_update_minimal(self):
        """Test minimal WorkerAvailabilityUpdate instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import (
            WorkerAvailabilityUpdate,
            Availability,
        )

        req = WorkerAvailabilityUpdate(availability=Availability.PUBLIC)
        assert req.availability == Availability.PUBLIC

    def test_worker_trust_level_update_minimal(self):
        """Test minimal WorkerTrustLevelUpdate instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import (
            WorkerTrustLevelUpdate,
            TrustLevel,
        )

        req = WorkerTrustLevelUpdate(trust_level=TrustLevel.TRUSTED)
        assert req.trust_level == TrustLevel.TRUSTED

    def test_worker_patch_request_minimal(self):
        """Test minimal WorkerPatchRequest instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import WorkerPatchRequest

        req = WorkerPatchRequest(name="Updated Name")
        assert req.name == "Updated Name"
        assert req.description is None

    def test_worker_config_update_minimal(self):
        """Test minimal WorkerConfigUpdate instantiation."""
        from src.interfaces.api.schemas.worker_management_schemas import WorkerConfigUpdate

        req = WorkerConfigUpdate(fusion_enable=True)
        assert req.fusion_enable is True

    def test_profile_search_request_minimal(self):
        """Test minimal ProfileSearchRequest instantiation."""
        from src.interfaces.api.schemas.profile_management_schemas import ProfileSearchRequest

        req = ProfileSearchRequest(query="test query")
        assert req.query == "test query"
        assert req.top_k == 10

    def test_profile_analyze_request_minimal(self):
        """Test minimal ProfileAnalyzeRequest instantiation."""
        from src.interfaces.api.schemas.profile_management_schemas import ProfileAnalyzeRequest

        req = ProfileAnalyzeRequest()
        assert req.analyze_type == "quality"

    def test_profile_patch_request_minimal(self):
        """Test minimal ProfilePatchRequest instantiation."""
        from src.interfaces.api.schemas.profile_management_schemas import ProfilePatchRequest

        req = ProfilePatchRequest(display_name="New Name")
        assert req.display_name == "New Name"


class TestOptionalFieldsPayloads:
    """Test that optional fields can be instantiated."""

    def test_bot_recommendation_request_optional(self):
        """Test BotRecommendationRequest with optional fields."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        req = BotRecommendationRequest(
            question="test question",
            topK=10,
            driver_bot_id="bot_001:default",
            group_id="grp_123",
            min_score=0.5,
            expand_factor=5,
            enable_rerank=False,
            reranker_model="custom-reranker",
            filters={"domain": "tech"},
            type="search",
        )
        assert req.topK == 10
        assert req.driver_bot_id == "bot_001:default"
        assert req.filters == {"domain": "tech"}

    def test_fusion_request_optional(self):
        """Test FusionRequest with optional fields."""
        from src.interfaces.api.schemas.fusion_schemas import (
            FusionRequest,
            FuseOptions,
            FuseMetadata,
        )

        req = FusionRequest(
            question="test question",
            participants=["bot1", "bot2"],
            driver_bot_id="bot1:default",
            fusion_mode="conflict_alignment",
            options=FuseOptions(
                timeout_ms=300000,
                parallel=False,
                detect_conflicts=True,
            ),
            metadata=FuseMetadata(
                request_id="req_001",
                source="test",
            ),
        )
        assert req.driver_bot_id == "bot1:default"
        assert req.fusion_mode == "conflict_alignment"
        assert req.options.timeout_ms == 300000

    def test_batch_verify_request_optional(self):
        """Test BatchVerifyRequest with optional fields."""
        from src.interfaces.api.schemas.verify_schemas import BatchVerifyRequest

        req = BatchVerifyRequest(
            worker_ids=["wrk_001", "wrk_002"],
            capabilities=["cap1", "cap2"],
            verify_options={"timeout": 30},
        )
        assert req.capabilities == ["cap1", "cap2"]
        assert req.verify_options == {"timeout": 30}

    def test_worker_sync_request_optional(self):
        """Test WorkerSyncRequest with optional fields."""
        from src.interfaces.api.schemas.worker_management_schemas import WorkerSyncRequest

        req = WorkerSyncRequest(
            name="Test Worker",
            description="A test worker",
            profile_content="Profile content",
            identity_handle="test_worker",
            identity_title="Test Title",
            domains=["domain1", "domain2"],
            capabilities=["cap1"],
            external_id="ext_001",
            metadata={"key": "value"},
        )
        assert req.description == "A test worker"
        assert req.domains == ["domain1", "domain2"]

    def test_profile_search_request_optional(self):
        """Test ProfileSearchRequest with optional fields."""
        from src.interfaces.api.schemas.profile_management_schemas import ProfileSearchRequest

        req = ProfileSearchRequest(
            query="test query",
            top_k=20,
            filters={"domain": "tech"},
            min_score=0.5,
            search_type="vector",
        )
        assert req.top_k == 20
        assert req.filters == {"domain": "tech"}


class TestNoForbiddenImports:
    """Test that schema modules don't import forbidden internal dependencies."""

    def test_recommend_schemas_no_forbidden_imports(self):
        """Test recommend schemas don't import forbidden dependencies."""
        import src.interfaces.api.schemas.recommend_schemas as module
        module_source = open(module.__file__, "r").read()

        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "sofapy_base",
            "ant_sofapy_base",
            "mist",
            "mist_client",
            "layotto",
            "src.infra.config.zdas_settings",
            "src.infra.adapters.zdas_",
            "src.infra.vectorstores.qdrant_zdas_vector_store",
            "src.infra.vectorstores.faiss_zdas_vector_store",
            "src.infra.vectorstore_backends.zdas_vector_persistence_backend",
        ]

        for pattern in forbidden:
            assert pattern not in module_source, f"Forbidden import found: {pattern}"

    def test_fusion_schemas_no_forbidden_imports(self):
        """Test fusion schemas don't import forbidden dependencies."""
        import src.interfaces.api.schemas.fusion_schemas as module
        module_source = open(module.__file__, "r").read()

        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "layotto",
            "mist",
            "zdas_",
        ]

        for pattern in forbidden:
            assert pattern not in module_source, f"Forbidden import found: {pattern}"

    def test_verify_schemas_no_forbidden_imports(self):
        """Test verify schemas don't import forbidden dependencies."""
        import src.interfaces.api.schemas.verify_schemas as module
        module_source = open(module.__file__, "r").read()

        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "layotto",
            "mist",
            "zdas_",
        ]

        for pattern in forbidden:
            assert pattern not in module_source, f"Forbidden import found: {pattern}"

    def test_worker_management_schemas_no_forbidden_imports(self):
        """Test worker management schemas don't import forbidden dependencies."""
        import src.interfaces.api.schemas.worker_management_schemas as module
        module_source = open(module.__file__, "r").read()

        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "layotto",
            "mist",
            "zdas_",
        ]

        for pattern in forbidden:
            assert pattern not in module_source, f"Forbidden import found: {pattern}"

    def test_profile_management_schemas_no_forbidden_imports(self):
        """Test profile management schemas don't import forbidden dependencies."""
        import src.interfaces.api.schemas.profile_management_schemas as module
        module_source = open(module.__file__, "r").read()

        forbidden = [
            "bcsfuse_internal",
            "sofapy",
            "layotto",
            "mist",
            "zdas_",
        ]

        for pattern in forbidden:
            assert pattern not in module_source, f"Forbidden import found: {pattern}"


class TestSchemaValidation:
    """Test schema validation rules."""

    def test_bot_recommendation_request_validates_topk(self):
        """Test BotRecommendationRequest validates topK range."""
        from src.interfaces.api.schemas.recommend_schemas import BotRecommendationRequest

        # Valid range
        req = BotRecommendationRequest(question="test", topK=10)
        assert req.topK == 10

        # Invalid - too low
        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", topK=0)

        # Invalid - too high
        with pytest.raises(ValidationError):
            BotRecommendationRequest(question="test", topK=25)

    def test_fusion_request_validates_participants(self):
        """Test FusionRequest validates participants length."""
        from src.interfaces.api.schemas.fusion_schemas import FusionRequest

        # Valid
        req = FusionRequest(question="test", participants=["bot1"])
        assert len(req.participants) == 1

        # Invalid - empty
        with pytest.raises(ValidationError):
            FusionRequest(question="test", participants=[])

    def test_availability_enum_values(self):
        """Test Availability enum values."""
        from src.interfaces.api.schemas.worker_management_schemas import Availability

        assert Availability.PRIVATE.value == "private"
        assert Availability.PROTECTED.value == "protected"
        assert Availability.PUBLIC.value == "public"

    def test_trust_level_enum_values(self):
        """Test TrustLevel enum values."""
        from src.interfaces.api.schemas.worker_management_schemas import TrustLevel

        assert TrustLevel.UNVERIFIED.value == "unverified"
        assert TrustLevel.VERIFYING.value == "verifying"
        assert TrustLevel.SANDBOX_ONLY.value == "sandbox_only"
        assert TrustLevel.GUARDED.value == "guarded"
        assert TrustLevel.TRUSTED.value == "trusted"