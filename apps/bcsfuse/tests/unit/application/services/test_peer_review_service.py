"""PeerReviewService 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.services.peer_review_service import PeerReviewService
from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.verify_dto import (
    CapabilityProbes,
    DimensionJudgment,
    DimensionProbe,
    DimensionResult,
    PeerReviewItem,
    PeerReviewResult,
    PeerReviewer,
    VerifyData,
)
from src.domain.models.worker import (
    Availability,
    Capability,
    CapabilityLevel,
    TrustLevel,
    Worker,
    WorkerIdentity,
    WorkerLifecycleState,
    WorkerSourceType,
    WorkerState,
)


def _make_worker(
    worker_id: str = "wrk_test",
    name: str = "Test Bot",
    capabilities: list[str] | None = None,
    external_id: str = "bot-test-uuid",
) -> Worker:
    caps = [
        Capability(name=n, level=CapabilityLevel.INTERMEDIATE)
        for n in (capabilities or ["数据库运维"])
    ]
    return Worker(
        id=worker_id,
        type="bot",
        identity=WorkerIdentity(name=name, handle=name.lower().replace(" ", "_")),
        responsibilities=["测试"],
        capabilities=caps,
        state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.UNVERIFIED),
        lifecycle_state=WorkerLifecycleState.ACTIVE,
        source_type=WorkerSourceType.API,
        external_id=external_id,
    )


def _make_verify_data(worker_id: str = "wrk_test") -> VerifyData:
    return VerifyData(
        worker_id=worker_id,
        capabilities=[Capability(name="数据库运维", level=CapabilityLevel.INTERMEDIATE)],
        soul_md="我是一个数据库运维 bot",
    )


def _make_recommendation_response(
    recommendations: list[dict] | None = None,
) -> CandidateRecommendationResponse:
    """构造 CandidateRecommendationResponse mock。"""
    if recommendations is None:
        recommendations = []

    recs = []
    for r in recommendations:
        recs.append(CandidateRecommendation(
            profile_key=r.get("profile_key", f"profile_{r['worker_id']}"),
            worker_id=r["worker_id"],
            score=r.get("score", 0.9),
            reasons=r.get("reasons", []),
            domain=r.get("domain", "general"),
            matched_skills=r.get("matched_skills", []),
            is_supplement=r.get("is_supplement", True),
        ))
    return CandidateRecommendationResponse(
        recommendations=recs,
        question="test",
        mode="agent",
    )


class TestFindPeerReviewers:
    @pytest.mark.asyncio
    async def test_high_score_returns_reviewer(self) -> None:
        """score >= min_similarity 的推荐应被选为 peer reviewer。"""
        tested = _make_worker(capabilities=["数据库运维", "故障排查"])

        # Mock recommendation service
        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([
            {"worker_id": "wrk_peer1", "score": 0.92, "matched_skills": ["数据库运维", "故障排查"]},
        ])

        # Mock worker_repo for external_id lookup
        mock_repo = MagicMock()
        peer_worker = _make_worker(worker_id="wrk_peer1", capabilities=["数据库运维"], external_id="bot-peer1")
        mock_repo.get_by_id.return_value = peer_worker

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=mock_repo,
            recommendation_service=mock_rec_service,
            top_k=2,
            min_similarity=0.8,
        )

        result = await service.find_peer_reviewers(tested)
        assert len(result) == 1
        assert result[0].worker_id == "wrk_peer1"
        assert result[0].similarity == 0.92

    @pytest.mark.asyncio
    async def test_below_threshold_excluded(self) -> None:
        """score < min_similarity 的推荐应被过滤。"""
        tested = _make_worker(capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([
            {"worker_id": "wrk_peer1", "score": 0.5},
        ])

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=mock_rec_service,
            top_k=2,
            min_similarity=0.8,
        )

        result = await service.find_peer_reviewers(tested)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_excludes_self(self) -> None:
        """推荐结果中包含自身时应排除。"""
        tested = _make_worker(worker_id="wrk_self", capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([
            {"worker_id": "wrk_self", "score": 1.0},
        ])

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=mock_rec_service,
            top_k=2,
            min_similarity=0.8,
        )

        result = await service.find_peer_reviewers(tested)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_excludes_workers_without_external_id(self) -> None:
        """没有 external_id 的 worker 不能做 peer reviewer。"""
        tested = _make_worker(capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([
            {"worker_id": "wrk_peer1", "score": 0.9},
        ])

        mock_repo = MagicMock()
        peer_worker = _make_worker(worker_id="wrk_peer1", capabilities=["数据库运维"], external_id="")
        mock_repo.get_by_id.return_value = peer_worker

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=mock_repo,
            recommendation_service=mock_rec_service,
            top_k=2,
            min_similarity=0.8,
        )

        result = await service.find_peer_reviewers(tested)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_returns_top_k(self) -> None:
        """应该只返回 top_k 个。"""
        tested = _make_worker(capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([
            {"worker_id": f"wrk_p{i}", "score": 0.95 - i * 0.01} for i in range(5)
        ])

        mock_repo = MagicMock()
        def get_worker(wid):
            return _make_worker(worker_id=wid, capabilities=["数据库运维"], external_id=f"bot-{wid}")
        mock_repo.get_by_id.side_effect = get_worker

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=mock_repo,
            recommendation_service=mock_rec_service,
            top_k=2,
            min_similarity=0.8,
        )

        result = await service.find_peer_reviewers(tested)
        assert len(result) == 2
        # Should be sorted by score descending (top_k * 3 = 6, but only 5 are returned)
        assert result[0].similarity >= result[1].similarity

    @pytest.mark.asyncio
    async def test_no_recommendations(self) -> None:
        """推荐服务返回空时应返回空列表。"""
        tested = _make_worker(capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([])

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=mock_rec_service,
        )

        result = await service.find_peer_reviewers(tested)
        assert result == []

    @pytest.mark.asyncio
    async def test_recommendation_service_exception(self) -> None:
        """推荐服务抛异常时应返回空列表而不是崩溃。"""
        tested = _make_worker(capabilities=["数据库运维"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.side_effect = RuntimeError("service unavailable")

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=mock_rec_service,
        )

        result = await service.find_peer_reviewers(tested)
        assert result == []

    @pytest.mark.asyncio
    async def test_query_uses_llm_capabilities(self) -> None:
        """query 应优先使用 LLM 能力标签。"""
        tested = _make_worker(capabilities=["general"])

        mock_rec_service = MagicMock()
        mock_rec_service.recommend.return_value = _make_recommendation_response([])

        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=mock_rec_service,
        )

        await service.find_peer_reviewers(
            tested, llm_capabilities=["数据库运维", "故障排查"]
        )

        # 验证 recommend 被调用了
        mock_rec_service.recommend.assert_called_once()
        # question 是位置参数 (第一个参数)
        call_args = mock_rec_service.recommend.call_args
        query = call_args[0][0] if call_args[0] else call_args[1].get("question", "")
        assert "数据库运维" in query


class TestConductPeerReview:
    @pytest.mark.asyncio
    async def test_conduct_peer_review_success(self) -> None:
        """完整的 peer review 流程：生成问题 → 回答 → 评判。"""
        executor = AsyncMock()
        executor.chat.side_effect = [
            '[{"question": "请解释数据库索引的 B+ 树结构", "target_capability": "数据库运维"}]',
            "被测 bot 的回答内容",
            '{"confidence": 0.85, "reasoning": "回答正确且深入"}',
        ]

        service = PeerReviewService(
            executor=executor,
            worker_repo=MagicMock(),
            recommendation_service=MagicMock(),
        )

        reviewers = [
            PeerReviewer(
                worker_id="wrk_peer1",
                bot_uuid="bot-peer1",
                similarity=0.9,
                overlap_capabilities=["数据库运维"],
            )
        ]

        verify_data = _make_verify_data()
        results = await service.conduct_peer_review(
            tested_bot_uuid="bot-test",
            bot_intro="我是数据库运维专家",
            verify_data=verify_data,
            peer_reviewers=reviewers,
        )

        assert len(results) == 1
        assert results[0].peer_worker_id == "wrk_peer1"
        assert results[0].similarity == 0.9
        assert len(results[0].items) == 1
        assert results[0].items[0].question == "请解释数据库索引的 B+ 树结构"
        assert results[0].items[0].tested_bot_answer == "被测 bot 的回答内容"
        assert results[0].overall_confidence == 0.85

    @pytest.mark.asyncio
    async def test_peer_fails_to_generate_questions(self) -> None:
        """当 peer bot 无法生成问题时，应返回空 items。"""
        executor = AsyncMock()
        executor.chat.return_value = ""

        service = PeerReviewService(
            executor=executor,
            worker_repo=MagicMock(),
            recommendation_service=MagicMock(),
        )

        reviewers = [
            PeerReviewer(worker_id="wrk_peer1", bot_uuid="bot-peer1", similarity=0.9),
        ]

        results = await service.conduct_peer_review(
            tested_bot_uuid="bot-test",
            bot_intro="",
            verify_data=_make_verify_data(),
            peer_reviewers=reviewers,
        )

        assert len(results) == 1
        assert len(results[0].items) == 0
        assert results[0].overall_confidence == 0.0

    @pytest.mark.asyncio
    async def test_multiple_reviewers(self) -> None:
        """多个 peer reviewer 应该各自独立进行面试。"""
        executor = AsyncMock()
        executor.chat.side_effect = [
            '[{"question": "Q1", "target_capability": "c1"}]',
            "A1",
            '{"confidence": 0.9, "reasoning": "good"}',
            '[{"question": "Q2", "target_capability": "c2"}]',
            "A2",
            '{"confidence": 0.7, "reasoning": "ok"}',
        ]

        service = PeerReviewService(
            executor=executor,
            worker_repo=MagicMock(),
            recommendation_service=MagicMock(),
        )

        reviewers = [
            PeerReviewer(worker_id="wrk_p1", bot_uuid="bot-p1", similarity=0.9),
            PeerReviewer(worker_id="wrk_p2", bot_uuid="bot-p2", similarity=0.85),
        ]

        results = await service.conduct_peer_review(
            tested_bot_uuid="bot-test",
            bot_intro="intro",
            verify_data=_make_verify_data(),
            peer_reviewers=reviewers,
        )

        assert len(results) == 2
        assert results[0].overall_confidence == 0.9
        assert results[1].overall_confidence == 0.7


class TestToJudgments:
    def test_converts_peer_results_to_judgments(self) -> None:
        """to_judgments 应将 PeerReviewResult 转换为 (capability, confidence, reasoning) 元组。"""
        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=MagicMock(),
        )

        peer_results = [
            PeerReviewResult(
                peer_worker_id="wrk_p1",
                peer_bot_uuid="bot-p1",
                similarity=0.9,
                items=[
                    PeerReviewItem(
                        question="Q1",
                        tested_bot_answer="A1",
                        peer_evaluation="Good",
                        confidence=0.85,
                    ),
                ],
                overall_confidence=0.85,
                reasoning="Good",
            ),
        ]

        judgments = service.to_judgments(peer_results)
        assert len(judgments) == 1
        assert judgments[0][0] == "peer_review"
        assert judgments[0][1] == 0.85
        assert judgments[0][2] == "Good"

    def test_empty_items_falls_back_to_overall(self) -> None:
        """当 items 为空时，应使用 overall_confidence 和 reasoning。"""
        service = PeerReviewService(
            executor=AsyncMock(),
            worker_repo=MagicMock(),
            recommendation_service=MagicMock(),
        )

        peer_results = [
            PeerReviewResult(
                peer_worker_id="wrk_p1",
                peer_bot_uuid="bot-p1",
                similarity=0.9,
                items=[],
                overall_confidence=0.5,
                reasoning="未生成问题",
            ),
        ]

        judgments = service.to_judgments(peer_results)
        assert len(judgments) == 1
        assert judgments[0][1] == 0.5


class TestBuildQuery:
    def test_uses_llm_capabilities_first(self) -> None:
        worker = _make_worker(capabilities=["general"])
        query = PeerReviewService._build_query(worker, llm_capabilities=["数据库运维", "故障排查"])
        assert "数据库运维" in query
        assert "故障排查" in query

    def test_uses_worker_capabilities_as_fallback(self) -> None:
        worker = _make_worker(capabilities=["SQL优化", "性能调优"])
        query = PeerReviewService._build_query(worker)
        assert "SQL优化" in query
        assert "性能调优" in query

    def test_includes_description_and_soul_md(self) -> None:
        worker = _make_worker()
        worker.identity.description = "一个专业的数据库运维机器人"
        query = PeerReviewService._build_query(worker, soul_md="我是数据库专家")
        assert "数据库运维" in query
        assert "专业的" in query

    def test_returns_empty_when_nothing(self) -> None:
        worker = Worker(
            id="wrk_empty",
            type="bot",
            identity=WorkerIdentity(name="Empty", handle="empty"),
            responsibilities=[],
            capabilities=[],
            state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.UNVERIFIED),
        )
        query = PeerReviewService._build_query(worker)
        assert query == ""


class TestParseHelpers:
    def test_parse_questions_valid_json(self) -> None:
        raw = '[{"question": "Q1", "target_capability": "c1"}, {"question": "Q2", "target_capability": "c2"}]'
        items = PeerReviewService._parse_questions(raw)
        assert len(items) == 2
        assert items[0].question == "Q1"
        assert items[0].target_capability == "c1"

    def test_parse_questions_json_with_prefix(self) -> None:
        raw = 'Here are the questions:\n[{"question": "Q1", "target_capability": "c1"}]'
        items = PeerReviewService._parse_questions(raw)
        assert len(items) == 1

    def test_parse_questions_invalid_json(self) -> None:
        items = PeerReviewService._parse_questions("not json at all")
        assert items == []

    def test_parse_questions_empty(self) -> None:
        assert PeerReviewService._parse_questions("") == []
        assert PeerReviewService._parse_questions(None) == []

    def test_parse_evaluation_valid(self) -> None:
        raw = '{"confidence": 0.75, "reasoning": "部分正确"}'
        conf, reason = PeerReviewService._parse_evaluation(raw)
        assert conf == 0.75
        assert reason == "部分正确"

    def test_parse_evaluation_clamps_confidence(self) -> None:
        conf, _ = PeerReviewService._parse_evaluation('{"confidence": 1.5, "reasoning": ""}')
        assert conf == 1.0

        conf, _ = PeerReviewService._parse_evaluation('{"confidence": -0.5, "reasoning": ""}')
        assert conf == 0.0

    def test_parse_evaluation_invalid_json(self) -> None:
        raw = "not valid json"
        conf, reason = PeerReviewService._parse_evaluation(raw)
        assert conf == 0.0
        assert reason == "not valid json"


class TestCapabilityVerifyServiceWithPeerReview:
    @pytest.mark.asyncio
    async def test_peer_review_integrated_in_verify(self) -> None:
        """当 peer review service 存在时，_verify 应先尝试 peer review。"""
        from src.application.services.capability_verify_service import CapabilityVerifyService

        mock_prompt_composer = AsyncMock()
        mock_prompt_composer.compose.return_value = [
            CapabilityProbes(
                capability_name="数据库运维",
                dimensions=[
                    DimensionProbe(dimension="syntax", probe_prompt="Write a query"),
                    DimensionProbe(dimension="debug", probe_prompt="Find the bug"),
                    DimensionProbe(dimension="algo", probe_prompt="Explain B+ tree"),
                ],
            )
        ]

        mock_executor = AsyncMock()
        mock_executor.send_intro.return_value = "I am a DB bot"
        mock_executor.execute.return_value = [
            DimensionResult(capability_name="数据库运维", dimension="syntax", probe_prompt="Write a query", response_content="OK", failed=False),
            DimensionResult(capability_name="数据库运维", dimension="debug", probe_prompt="Find the bug", response_content="OK", failed=False),
            DimensionResult(capability_name="数据库运维", dimension="algo", probe_prompt="Explain B+ tree", response_content="OK", failed=False),
        ]

        mock_judge = AsyncMock()
        mock_judge.judge.return_value = [
            DimensionJudgment(capability_name="数据库运维", dimension="syntax", confidence=0.9, reasoning="Good"),
            DimensionJudgment(capability_name="数据库运维", dimension="debug", confidence=0.8, reasoning="OK"),
            DimensionJudgment(capability_name="数据库运维", dimension="algo", confidence=0.7, reasoning="Fair"),
        ]

        mock_worker_repo = MagicMock()
        tested_worker = _make_worker(worker_id="wrk_test", capabilities=["数据库运维"])
        mock_worker_repo.get_by_id.return_value = tested_worker

        mock_profile_repo = MagicMock()
        mock_profile_repo.get.return_value = None

        mock_peer_service = AsyncMock()
        mock_peer_service.find_peer_reviewers.return_value = [
            PeerReviewer(worker_id="wrk_peer1", bot_uuid="bot-peer1", similarity=0.9)
        ]
        mock_peer_service.conduct_peer_review.return_value = [
            PeerReviewResult(
                peer_worker_id="wrk_peer1",
                peer_bot_uuid="bot-peer1",
                similarity=0.9,
                items=[
                    PeerReviewItem(question="Q1", tested_bot_answer="A1", peer_evaluation="Good", confidence=0.8),
                ],
                overall_confidence=0.8,
                reasoning="Good",
            )
        ]
        mock_peer_service.to_judgments = MagicMock(return_value=[
            ("peer_review", 0.8, "Good"),
        ])

        service = CapabilityVerifyService(
            prompt_composer=mock_prompt_composer,
            executor=mock_executor,
            judge=mock_judge,
            worker_repo=mock_worker_repo,
            profile_repo=mock_profile_repo,
            peer_review_service=mock_peer_service,
        )

        from src.domain.events.worker_profile_created_event import WorkerProfileCreatedEvent
        event = WorkerProfileCreatedEvent(worker_id="wrk_test")
        await service._verify(event)

        mock_peer_service.find_peer_reviewers.assert_called_once()
        mock_peer_service.conduct_peer_review.assert_called_once()
        mock_worker_repo.update_trust_level.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_when_no_peer_reviewers(self) -> None:
        """当没有 peer reviewer 时，应回退到现有 LLM judge 流程。"""
        from src.application.services.capability_verify_service import CapabilityVerifyService

        mock_prompt_composer = AsyncMock()
        mock_prompt_composer.compose.return_value = [
            CapabilityProbes(
                capability_name="数据库运维",
                dimensions=[
                    DimensionProbe(dimension="syntax", probe_prompt="Write a query"),
                    DimensionProbe(dimension="debug", probe_prompt="Find the bug"),
                    DimensionProbe(dimension="algo", probe_prompt="Explain B+ tree"),
                ],
            )
        ]

        mock_executor = AsyncMock()
        mock_executor.send_intro.return_value = "I am a DB bot"
        mock_executor.execute.return_value = [
            DimensionResult(capability_name="数据库运维", dimension="syntax", probe_prompt="Write a query", response_content="OK", failed=False),
            DimensionResult(capability_name="数据库运维", dimension="debug", probe_prompt="Find the bug", response_content="OK", failed=False),
            DimensionResult(capability_name="数据库运维", dimension="algo", probe_prompt="Explain B+ tree", response_content="OK", failed=False),
        ]

        mock_judge = AsyncMock()
        mock_judge.judge.return_value = [
            DimensionJudgment(capability_name="数据库运维", dimension="syntax", confidence=0.9, reasoning="Good"),
            DimensionJudgment(capability_name="数据库运维", dimension="debug", confidence=0.8, reasoning="OK"),
            DimensionJudgment(capability_name="数据库运维", dimension="algo", confidence=0.7, reasoning="Fair"),
        ]

        mock_worker_repo = MagicMock()
        tested_worker = _make_worker(worker_id="wrk_test", capabilities=["数据库运维"])
        mock_worker_repo.get_by_id.return_value = tested_worker

        mock_profile_repo = MagicMock()
        mock_profile_repo.get.return_value = None

        mock_peer_service = AsyncMock()
        mock_peer_service.find_peer_reviewers.return_value = []

        service = CapabilityVerifyService(
            prompt_composer=mock_prompt_composer,
            executor=mock_executor,
            judge=mock_judge,
            worker_repo=mock_worker_repo,
            profile_repo=mock_profile_repo,
            peer_review_service=mock_peer_service,
        )

        from src.domain.events.worker_profile_created_event import WorkerProfileCreatedEvent
        event = WorkerProfileCreatedEvent(worker_id="wrk_test")
        await service._verify(event)

        mock_peer_service.find_peer_reviewers.assert_called_once()
        mock_peer_service.conduct_peer_review.assert_not_called()
        mock_judge.judge.assert_called_once()
        mock_worker_repo.update_trust_level.assert_called_once()