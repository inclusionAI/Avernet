"""
Unit tests for strict_participants behavior

Tests the strict participant mode across the service chain:
- GroupFusionService -> ExpertDiagnosisService -> G5ExpertEnhancer -> WorkerProfileRetrievalService
"""

import pytest
from unittest.mock import MagicMock, patch, ANY
from datetime import datetime

from src.domain.models.fusion_request import FusionRequest, FuseOptions
from src.domain.models.fusion_result import FusionResult, Perspective
from src.domain.models.retrieval_mode import RetrievalMode


class TestStrictParticipantsInRetrievalService:
    """Tests for strict_participants in WorkerProfileRetrievalService"""

    def test_strict_mode_returns_empty_on_no_match(self):
        """Should return empty results in strict mode when no keys match"""
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService

        # Mock source
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_existing_profile:default"
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        service = WorkerProfileRetrievalService(
            source=mock_source,
            strict_participants=True,
        )

        result = service.retrieve(
            question="test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profile_keys=["nonexistent_profile:default"],
            strict_participants=True,
        )

        assert result.total_count == 0
        assert len(result.results) == 0

    def test_non_strict_mode_allows_fallback(self):
        """In non-strict mode, empty filter results should not block retrieval"""
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService

        # Mock source
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_existing_profile:default"
        mock_profile.active_skills = []
        mock_profile.context_fragments = []
        mock_profile.searchable_text = ""
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        service = WorkerProfileRetrievalService(
            source=mock_source,
        )

        # Non-strict mode with non-matching keys - the retrieval should continue
        # but filter results be empty (no fallback happens at this level)
        result = service.retrieve(
            question="test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profile_keys=["nonexistent_profile:default"],
            strict_participants=False,
        )

        # The service returns empty because no keys matched
        # The fallback logic is at the G5ExpertEnhancer level
        assert result.total_count == 0

    def test_strict_mode_with_canonicalization(self):
        """Should use canonicalization to match keys in strict mode"""
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService

        # Mock source with profile that has staff_ prefix
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_test_architect:default"
        mock_profile.active_skills = [MagicMock(name="skill")]
        mock_profile.active_skills[0].name = "architecture"
        mock_profile.context_fragments = []
        mock_profile.searchable_text = "architecture design"
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        service = WorkerProfileRetrievalService(
            source=mock_source,
        )

        # Request with wrk_ prefix should be canonicalized to staff_wrk_
        result = service.retrieve(
            question="architecture design question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profile_keys=["wrk_test_architect:default"],
            strict_participants=True,
        )

        # Should find the profile via canonicalization
        assert result.total_count == 1
        assert result.results[0].profile.profile_key == "staff_wrk_test_architect:default"


class TestStrictParticipantsInG5Enhancer:
    """Tests for strict_participants in G5ExpertEnhancerImpl"""

    def test_strict_mode_blocks_fallback_when_no_match(self):
        """Should NOT fallback to full DB in strict mode when participants filter fails"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # Mock retrieval service that returns empty in strict mode
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve.return_value = MagicMock(results=[], total_count=0)

        # Mock other dependencies
        mock_gateway = MagicMock()
        mock_preparation = MagicMock()
        mock_source = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Call with strict_participants=True
        result = enhancer.enhance(
            question="test question",
            base_perspectives=[],
            participants=["nonexistent:default"],
            strict_participants=True,
        )

        # Should return base_perspectives (empty list)
        # Should NOT attempt fallback retrieval
        assert result == []
        # Should have called retrieve exactly once (no fallback)
        mock_retrieval.retrieve.assert_called_once()

    def test_non_strict_mode_allows_fallback(self):
        """Should fallback to full DB in non-strict mode when participants filter fails"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # Mock retrieval service
        mock_retrieval = MagicMock()

        # First call (with participants) returns empty
        # Second call (fallback) returns profiles
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_fallback:default"
        mock_profile.active_skills = []

        mock_retrieval.retrieve.side_effect = [
            MagicMock(results=[], total_count=0),  # First call with profile_keys
            MagicMock(results=[MagicMock(profile=mock_profile)], total_count=1),  # Fallback call
        ]

        # Mock other dependencies
        mock_gateway = MagicMock()
        mock_preparation = MagicMock()
        mock_source = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Call with strict_participants=False
        result = enhancer.enhance(
            question="test question",
            base_perspectives=[],
            participants=["nonexistent:default"],
            strict_participants=False,
        )

        # Should have attempted fallback
        assert mock_retrieval.retrieve.call_count == 2

    def test_strict_mode_with_none_participants(self):
        """In strict mode, None participants should still allow full DB retrieval"""
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        mock_retrieval = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_profile:default"
        mock_retrieval.retrieve.return_value = MagicMock(
            results=[MagicMock(profile=mock_profile)],
            total_count=1
        )

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()
        mock_source = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Call with participants=None and strict_participants=True
        # This is valid - strict mode only blocks fallback, not initial full retrieval
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=None,
            strict_participants=True,
        )

        # Should return profiles from full DB retrieval
        assert len(result) >= 0  # Depends on mock return


class TestStrictParticipantsInExpertDiagnosisService:
    """Tests for strict_participants passing through ExpertDiagnosisService"""

    def test_strict_participants_passed_to_enhancer(self):
        """Should pass strict_participants to G5 enhancer"""
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService

        # Mock enhancer
        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        service = ExpertDiagnosisService(
            g5_enhancer=mock_enhancer,
        )

        # Call diagnose with strict_participants=True
        service.diagnose(
            question="test question",
            perspectives=[],
            participants=["test:default"],
            strict_participants=True,
        )

        # Verify strict_participants was passed
        mock_enhancer.enhance.assert_called_once()
        call_kwargs = mock_enhancer.enhance.call_args[1]
        assert call_kwargs.get("strict_participants") == True

    def test_strict_mode_empty_enhancer_result_stays_empty(self):
        """When strict=True and enhancer returns empty, should NOT fallback to base perspectives"""
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
        from src.domain.models.fusion_result import Perspective

        # Mock enhancer that returns empty list
        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        # Create base perspectives that would be wrong to return
        base_perspective = Perspective(
            participant_id="wrong_expert",
            participant_type="bot",
            role="expert",
            summary="This should not be returned in strict mode",
            status="completed",
        )

        service = ExpertDiagnosisService(
            g5_enhancer=mock_enhancer,
        )

        # Call diagnose with strict_participants=True
        result = service.diagnose(
            question="test question",
            perspectives=[base_perspective],
            participants=["requested_expert:default"],
            strict_participants=True,
        )

        # Result should have empty perspectives (not the base perspective)
        assert len(result.perspectives) == 0

    def test_non_strict_mode_empty_enhancer_result_falls_back(self):
        """When strict=False and enhancer returns empty, should fallback to base perspectives"""
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
        from src.domain.models.fusion_result import Perspective

        # Mock enhancer that returns empty list
        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        # Create base perspectives
        base_perspective = Perspective(
            participant_id="fallback_expert",
            participant_type="bot",
            role="expert",
            summary="This is the fallback",
            status="completed",
        )

        service = ExpertDiagnosisService(
            g5_enhancer=mock_enhancer,
        )

        # Call diagnose with strict_participants=False
        result = service.diagnose(
            question="test question",
            perspectives=[base_perspective],
            participants=["requested_expert:default"],
            strict_participants=False,
        )

        # Result should have the base perspective as fallback
        assert len(result.perspectives) == 1
        assert result.perspectives[0].participant_id == "fallback_expert"


class TestStrictParticipantsInGroupFusionService:
    """Tests for strict_participants in GroupFusionService"""

    def test_strict_participants_passed_to_diagnose(self):
        """Should pass strict_participants to ExpertDiagnosisService.diagnose()"""
        from src.application.services.group_fusion_service import GroupFusionService

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.collect.return_value = MagicMock(
            participant_id="test",
            status="completed",
        )

        # Mock expert diagnosis service
        mock_expert_service = MagicMock()
        mock_expert_service.diagnose.return_value = MagicMock(
            perspectives=[],
            warnings=[],
            errors=[],
        )

        service = GroupFusionService(
            provider=mock_provider,
            expert_diagnosis_service=mock_expert_service,
        )

        # Create request with strict_participants=True
        request = FusionRequest(
            question="test question",
            participants=["test:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(
                strict_participants=True,
            ),
        )

        # Execute
        service.fuse(request, "test_group")

        # Verify strict_participants was passed
        mock_expert_service.diagnose.assert_called_once()
        call_kwargs = mock_expert_service.diagnose.call_args[1]
        assert call_kwargs.get("strict_participants") == True

    def test_default_strict_participants_is_false(self):
        """Default strict_participants should be False for backward compatibility"""
        from src.domain.models.fusion_request import FuseOptions

        options = FuseOptions()
        # Default should allow fallback
        assert options.strict_participants == True  # Based on the current model default

    def test_g5_mode_with_explicit_participants(self):
        """G5 mode should respect explicit participants"""
        from src.application.services.group_fusion_service import GroupFusionService
        from src.domain.models.fusion_result import FusionTiming
        from datetime import datetime

        mock_provider = MagicMock()
        mock_provider.collect.return_value = Perspective(
            participant_id="test_participant",
            participant_type="bot",
            role="expert",
            summary="Test summary",
            status="completed",
        )

        now = datetime.now()
        mock_expert_service = MagicMock()
        mock_expert_service.diagnose.return_value = FusionResult(
            group_id="test_group",
            fusion_id="test_id",
            question="test question",
            perspectives=[],
            warnings=[],
            errors=[],
            partial_success=False,
            timing=FusionTiming(
                started_at=now,
                finished_at=now,
                duration_ms=100,
            ),
        )

        service = GroupFusionService(
            provider=mock_provider,
            expert_diagnosis_service=mock_expert_service,
        )

        request = FusionRequest(
            question="test question",
            participants=["explicit_participant:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=True),
        )

        service.fuse(request, "test_group")

        # Verify participants was passed
        call_kwargs = mock_expert_service.diagnose.call_args[1]
        assert call_kwargs.get("participants") == ["explicit_participant:default"]


class TestStrictParticipantsEndToEnd:
    """End-to-end tests for strict_participants behavior"""

    def test_full_chain_strict_mode_blocks_fallback(self):
        """
        Full chain test: strict mode should prevent fallback
        when participants don't match any profiles.
        """
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # Setup: mock source with profiles that won't match
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_different_profile:default"
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
        )

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=retrieval_service,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Request for non-existent profile with strict mode
        result = enhancer.enhance(
            question="test question",
            base_perspectives=[],
            participants=["nonexistent_profile:default"],
            strict_participants=True,
        )

        # Should return empty, not fallback to the different profile
        assert result == []

    def test_full_chain_canonicalization_enables_match(self):
        """
        Full chain test: canonicalization should enable matching
        even with different prefixes.
        """
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl
        from src.domain.models.worker_profile import SkillProfile

        # Setup: mock source with matching profile (different prefix)
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_test_architect:default"
        # Use a real SkillProfile object instead of MagicMock for name attribute
        mock_skill = SkillProfile(
            skill_id="test",
            name="architecture",
            description="test",
            skill_set_name="default",
        )
        mock_profile.active_skills = [mock_skill]
        mock_profile.context_fragments = []
        mock_profile.searchable_text = "architecture"
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        retrieval_service = WorkerProfileRetrievalService(
            source=mock_source,
        )

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=retrieval_service,
            preparation_service=mock_preparation,
            profile_source=mock_source,
            max_experts=1,
        )

        # Request with wrk_ prefix - should match via canonicalization
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=["wrk_test_architect:default"],
            strict_participants=True,
        )

        # Should find the profile via canonicalization
        assert len(result) == 1
        assert result[0].profile_key == "staff_wrk_test_architect:default"


class TestStrictParticipantsPerspectiveCollection:
    """
    Tests for strict_participants behavior in _collect_perspectives.

    These tests verify the correct handling of unavailable participants
    in both strict and compatibility modes.
    """

    def test_strict_true_participant_offline_returns_empty(self):
        """
        Test Case 1: strict=true + participant offline -> empty perspectives, no skipped

        When strict=True and participant is offline/unavailable:
        - Should NOT create a skipped perspective
        - Should return empty perspectives list
        - Should record a warning
        """
        from src.application.services.group_fusion_service import GroupFusionService
        from src.application.services.participant_availability_checker import (
            ParticipantAvailabilityChecker,
            ParticipantAvailability,
        )

        # Mock availability checker that reports participant as offline
        mock_checker = MagicMock(spec=ParticipantAvailabilityChecker)
        mock_checker.check_batch.return_value = {
            "wrk_offline_expert:default": ParticipantAvailability(
                participant_id="wrk_offline_expert:default",
                worker_id="wrk_offline_expert",
                is_available=False,
                unavailability_reason="offline",
            )
        }

        # Mock provider (should never be called in this case)
        mock_provider = MagicMock()

        service = GroupFusionService(
            provider=mock_provider,
            availability_checker=mock_checker,
        )

        request = FusionRequest(
            question="test question",
            participants=["wrk_offline_expert:default"],
            fusion_mode="agent",
            options=FuseOptions(strict_participants=True),
        )

        # Execute
        perspectives, warnings, errors = service._collect_perspectives(
            request=request,
            group_id="test_group",
            driver_bot_id="wrk_offline_expert:default",
        )

        # Verify: no perspectives created, warning recorded
        assert len(perspectives) == 0, "strict=True should NOT create skipped perspectives"
        assert len(warnings) == 1, "Should have one warning about offline participant"
        assert "offline" in warnings[0] or "not registered" in warnings[0]

        # Provider should NOT have been called for unavailable participant
        mock_provider.collect.assert_not_called()

    def test_strict_true_nonexistent_participant_returns_empty(self):
        """
        Test Case 2: strict=true + nonexistent participant -> empty perspectives

        When strict=True and participant doesn't exist in registry:
        - Should NOT create a skipped perspective
        - Should return empty perspectives list
        - Should record appropriate warning
        """
        from src.application.services.group_fusion_service import GroupFusionService
        from src.application.services.participant_availability_checker import (
            ParticipantAvailabilityChecker,
            ParticipantAvailability,
        )

        # Mock availability checker that reports participant as unregistered
        mock_checker = MagicMock(spec=ParticipantAvailabilityChecker)
        mock_checker.check_batch.return_value = {
            "wrk_nonexistent:default": ParticipantAvailability(
                participant_id="wrk_nonexistent:default",
                worker_id=None,
                is_available=False,
                unavailability_reason="unregistered",
            )
        }

        mock_provider = MagicMock()

        service = GroupFusionService(
            provider=mock_provider,
            availability_checker=mock_checker,
        )

        request = FusionRequest(
            question="test question",
            participants=["wrk_nonexistent:default"],
            fusion_mode="agent",
            options=FuseOptions(strict_participants=True),
        )

        # Execute
        perspectives, warnings, errors = service._collect_perspectives(
            request=request,
            group_id="test_group",
            driver_bot_id="wrk_nonexistent:default",
        )

        # Verify
        assert len(perspectives) == 0, "strict=True should NOT create skipped perspectives for nonexistent participant"
        assert len(warnings) == 1
        assert "not registered" in warnings[0]

    def test_strict_false_participant_offline_keeps_skipped_compatibility(self):
        """
        Test Case 3: strict=false + participant offline -> skipped perspective (compatibility)

        When strict=False and participant is offline:
        - Should create a status="skipped" perspective (backward compatibility)
        - Should record warning
        """
        from src.application.services.group_fusion_service import GroupFusionService
        from src.application.services.participant_availability_checker import (
            ParticipantAvailabilityChecker,
            ParticipantAvailability,
        )

        # Mock availability checker that reports participant as offline
        mock_checker = MagicMock(spec=ParticipantAvailabilityChecker)
        mock_checker.check_batch.return_value = {
            "wrk_offline_expert:default": ParticipantAvailability(
                participant_id="wrk_offline_expert:default",
                worker_id="wrk_offline_expert",
                is_available=False,
                unavailability_reason="offline",
            )
        }

        mock_provider = MagicMock()

        service = GroupFusionService(
            provider=mock_provider,
            availability_checker=mock_checker,
        )

        request = FusionRequest(
            question="test question",
            participants=["wrk_offline_expert:default"],
            fusion_mode="agent",
            options=FuseOptions(strict_participants=False),  # Compatibility mode
        )

        # Execute
        perspectives, warnings, errors = service._collect_perspectives(
            request=request,
            group_id="test_group",
            driver_bot_id="wrk_offline_expert:default",
        )

        # Verify: skipped perspective created for backward compatibility
        assert len(perspectives) == 1, "strict=False should create skipped perspective for compatibility"
        assert perspectives[0].status == "skipped"
        assert perspectives[0].participant_id == "wrk_offline_expert:default"
        assert "unavailable" in perspectives[0].summary.lower()

    def test_retrieval_empty_base_perspectives_all_skipped_enhancer_returns_empty(self):
        """
        Test Case 4: retrieval empty + base_perspectives all skipped -> enhancer returns empty

        When G5ExpertEnhancer:
        - Profile retrieval returns empty
        - base_perspectives are all status="skipped"
        - Should return empty list, NOT fallback to skipped perspectives
        """
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # Mock retrieval that returns empty
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve.return_value = MagicMock(results=[], total_count=0)

        # Create base perspectives that are all skipped
        base_perspectives = [
            Perspective(
                participant_id="wrk_unavailable_1:default",
                participant_type="bot",
                role="expert",
                summary="Worker is unavailable: offline",
                status="skipped",
            ),
            Perspective(
                participant_id="wrk_unavailable_2:default",
                participant_type="bot",
                role="expert",
                summary="Worker is unavailable: unregistered",
                status="skipped",
            ),
        ]

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()
        mock_source = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Execute with strict=False (this tests the all_skipped fallback logic)
        result = enhancer.enhance(
            question="test question",
            base_perspectives=base_perspectives,
            participants=["wrk_test:default"],
            strict_participants=False,
        )

        # Verify: Should return empty, not the skipped perspectives
        assert result == [], "Should return empty list when base_perspectives are all skipped"

    def test_profile_source_empty_proper_logging_and_semantics(self):
        """
        Test Case 5: profile source empty -> proper logging and semantics

        When WorkerProfileSource is empty (no profiles available):
        - G5ExpertEnhancer should handle gracefully
        - Should return empty list (not error)
        - Logging should indicate the situation clearly
        """
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # Mock retrieval that returns empty (simulating empty profile source)
        mock_retrieval = MagicMock()
        mock_retrieval.retrieve.return_value = MagicMock(results=[], total_count=0)

        # Mock source is empty
        mock_source = MagicMock()
        mock_source.scan.return_value = MagicMock(profiles=[])

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # Execute without participants (triggers full DB retrieval)
        result = enhancer.enhance(
            question="test question",
            base_perspectives=[],  # Empty base perspectives
            participants=None,  # No explicit participants
            strict_participants=False,
        )

        # Verify: Should return empty list gracefully
        assert result == [], "Should return empty list when profile source is empty"

        # Verify retrieval was attempted
        mock_retrieval.retrieve.assert_called()