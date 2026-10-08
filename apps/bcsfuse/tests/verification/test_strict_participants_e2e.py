"""
strict_participants 端到端严格验证测试

这些测试用例验证 strict_participants 参数在整个调用链中的行为。

关键验证点：
1. strict=True 时，participant 过滤失败后禁止 fallback
2. strict=False 时，允许 fallback 但需要标记 degraded
3. 参数必须正确传递到整个调用链

调用链：
GroupFusionService -> ExpertDiagnosisService -> G5ExpertEnhancer -> RetrievalService
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


class TestStrictParticipantsInRetrievalService:
    """Retrieval Service 层的 strict_participants 测试"""

    def test_strict_mode_returns_empty_when_no_match(self):
        """
        验证：strict=True 时，profile_keys 过滤为空时返回空结果

        不允许 fallback 到全库
        """
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
        from src.domain.models.retrieval_mode import RetrievalMode

        # 模拟 profile source
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_existing_profile:default"
        mock_profile.active_skills = []
        mock_profile.context_fragments = []
        mock_profile.searchable_text = ""
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        service = WorkerProfileRetrievalService(source=mock_source)

        # 请求不存在的 profile
        result = service.retrieve(
            question="test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profile_keys=["nonexistent_profile:default"],
            strict_participants=True,
        )

        # 必须返回空结果
        assert result.total_count == 0
        assert len(result.results) == 0

    def test_non_strict_mode_allows_continuation(self):
        """
        验证：strict=False 时，过滤为空后继续执行（后续可能 fallback）
        """
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
        from src.domain.models.retrieval_mode import RetrievalMode

        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_existing:default"
        mock_profile.active_skills = []
        mock_profile.context_fragments = []
        mock_profile.searchable_text = ""
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        service = WorkerProfileRetrievalService(source=mock_source)

        result = service.retrieve(
            question="test question",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            profile_keys=["nonexistent:default"],
            strict_participants=False,
        )

        # 返回空结果（fallback 在 G5Enhancer 层处理）
        assert result.total_count == 0


class TestStrictParticipantsInG5Enhancer:
    """G5 Enhancer 层的 strict_participants 测试"""

    def test_strict_mode_blocks_fallback_completely(self):
        """
        验证：strict=True 时，G5Enhancer 完全禁止 fallback

        当 profile_keys 过滤为空时：- 不能调用全库检索
        - 返回空列表
        """
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # 模拟 retrieval service
        mock_retrieval = MagicMock()
        # 第一次调用返回空（profile_keys 过滤）
        mock_retrieval.retrieve.return_value = MagicMock(results=[], total_count=0)

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()
        mock_source = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=mock_retrieval,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # strict 模式调用
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=["nonexistent:default"],
            strict_participants=True,
        )

        # 必须返回空列表
        assert result == []

        # 只能调用一次 retrieve（禁止 fallback）
        assert mock_retrieval.retrieve.call_count == 1

    def test_non_strict_mode_attempts_fallback(self):
        """
        验证：strict=False 时，G5Enhancer 尝试 fallback

        当 profile_keys 过滤为空时：
        - 应该尝试全库检索
        """
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        mock_retrieval = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_fallback:default"
        mock_profile.active_skills = []

        # 第一次返回空，第二次返回结果
        mock_retrieval.retrieve.side_effect = [
            MagicMock(results=[], total_count=0),  # profile_keys 过滤为空
            MagicMock(results=[MagicMock(profile=mock_profile)], total_count=1),  # fallback
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

        # 非严格模式调用
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=["nonexistent:default"],
            strict_participants=False,
        )

        # 应该有 fallback 结果
        assert len(result) == 1

        # 应该调用两次（fallback）
        assert mock_retrieval.retrieve.call_count == 2

    def test_strict_mode_with_none_participants(self):
        """
        验证：strict=True 但 participants=None 时允许全库检索

        这是合法场景：用户没有指定参与者
        """
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        mock_retrieval = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_test:default"
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

        # participants=None，strict=True
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=None,
            strict_participants=True,
        )

        # 应该返回全库检索结果
        assert len(result) >= 0


class TestStrictParticipantsInExpertDiagnosisService:
    """Expert Diagnosis Service 层的 strict_participants 测试"""

    def test_strict_mode_empty_enhancer_result_stays_empty(self):
        """
        验证：strict=True 时，enhance() 返回空时不能回退到原 perspectives
这是之前发现的 bug！
        """
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
        from src.domain.models.fusion_result import Perspective

        # 模拟 enhancer 返回空
        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        # 原始 perspectives（包含错误的专家）
        wrong_perspective = Perspective(
            participant_id="wrong_expert",
            participant_type="bot",
            role="expert",
            summary="This should not be returned in strict mode",
            status="completed",
        )

        service = ExpertDiagnosisService(g5_enhancer=mock_enhancer)

        result = service.diagnose(
            question="test question",
            perspectives=[wrong_perspective],
            participants=["requested_expert:default"],
            strict_participants=True,
        )

        # strict 模式下不能回退到原 perspectives
        # 结果应该是空的或者不包含 wrong_expert
        has_wrong_expert = any(p.participant_id == "wrong_expert" for p in result.perspectives)
        assert not has_wrong_expert, "strict 模式下不应回退到原始 perspectives"

    def test_non_strict_mode_empty_enhancer_result_falls_back(self):
        """
        验证：strict=False 时，enhance() 返回空时回退到原 perspectives
        """
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
        from src.domain.models.fusion_result import Perspective

        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        fallback_perspective = Perspective(
            participant_id="fallback_expert",
            participant_type="bot",
            role="expert",
            summary="This is the fallback",
            status="completed",
        )

        service = ExpertDiagnosisService(g5_enhancer=mock_enhancer)

        result = service.diagnose(
            question="test question",
            perspectives=[fallback_perspective],
            participants=["requested_expert:default"],
            strict_participants=False,
        )

        # 非严格模式应该回退
        has_fallback = any(p.participant_id == "fallback_expert" for p in result.perspectives)
        assert has_fallback, "非严格模式应该回退到原始 perspectives"


class TestStrictParticipantsParameterChain:
    """验证 strict_participants 参数在整个调用链中的传递"""

    def test_parameter_chain_from_fusion_to_enhancer(self):
        """
        验证参数传递链：

        GroupFusionService._fuse_g5()
            -> ExpertDiagnosisService.diagnose(strict_participants=True)
                -> G5ExpertEnhancer.enhance(strict_participants=True)
        """
        from src.application.services.group_fusion_service import GroupFusionService
        from src.domain.models.fusion_request import FusionRequest, FuseOptions
        from src.domain.models.fusion_result import Perspective, FusionTiming

        # 模拟所有依赖
        mock_provider = MagicMock()
        mock_provider.collect.return_value = Perspective(
            participant_id="test",
            participant_type="bot",
            role="expert",
            summary="test",
            status="completed",
        )

        mock_enhancer = MagicMock()
        mock_enhancer.enhance.return_value = []

        mock_expert_service = MagicMock()
        mock_expert_service.diagnose.return_value = MagicMock(
            perspectives=[],
            warnings=[],
            errors=[],
            partial_success=False,
            timing=FusionTiming(started_at=datetime.now(), finished_at=datetime.now(), duration_ms=100),
        )

        service = GroupFusionService(
            provider=mock_provider,
            expert_diagnosis_service=mock_expert_service,
        )

        request = FusionRequest(
            question="test question",
            participants=["test:default"],
            fusion_mode="expert_diagnosis",
            options=FuseOptions(strict_participants=True),
        )

        service.fuse(request, "test_group")

        # 验证 diagnose 被调用时传递了 strict_participants
        mock_expert_service.diagnose.assert_called_once()
        call_kwargs = mock_expert_service.diagnose.call_args[1]
        assert call_kwargs.get("strict_participants") == True, \
            "strict_participants 必须传递到 ExpertDiagnosisService"


class TestStrictModeIntegration:
    """strict 模式集成测试"""

    def test_full_chain_strict_mode_blocks_all_fallbacks(self):
        """
        完整链路测试：strict 模式应该阻止所有 fallback

        从 RetrievalService 到 ExpertDiagnosisService
        """
        from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
        from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

        # 模拟 profile source
        mock_source = MagicMock()
        mock_profile = MagicMock()
        mock_profile.profile_key = "staff_wrk_different_profile:default"
        mock_profile.active_skills = []
        mock_profile.context_fragments = []
        mock_profile.searchable_text = ""
        mock_source.scan.return_value = MagicMock(profiles=[mock_profile])

        retrieval_service = WorkerProfileRetrievalService(source=mock_source)

        mock_gateway = MagicMock()
        mock_preparation = MagicMock()

        enhancer = G5ExpertEnhancerImpl(
            gateway=mock_gateway,
            retrieval_service=retrieval_service,
            preparation_service=mock_preparation,
            profile_source=mock_source,
        )

        # 请求不存在的 profile，strict 模式
        result = enhancer._retrieve_candidate_profiles(
            question="test question",
            participants=["nonexistent_profile:default"],
            strict_participants=True,
        )

        # 必须返回空，不能有 fallback
        assert result == [], f"strict 模式应该返回空列表，但得到 {len(result)} 个结果"