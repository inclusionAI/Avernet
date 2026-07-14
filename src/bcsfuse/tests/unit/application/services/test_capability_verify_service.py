"""CapabilityVerifyService 集成测试 + 映射逻辑测试。"""

from __future__ import annotations

import pytest

from src.application.services.capability_verify_service import CapabilityVerifyService
from src.domain.events.worker_profile_created_event import WorkerProfileCreatedEvent
from src.domain.models.verify_dto import (
    CapabilityProbes,
    DimensionJudgment,
    DimensionProbe,
    DimensionResult,
    VerifyData,
)
from src.domain.models.worker import Capability, CapabilityLevel, TrustLevel, Worker


class FakePromptComposer:
    async def compose(self, data: VerifyData) -> list[CapabilityProbes]:
        return [
            CapabilityProbes(
                capability_name=c.name,
                dimensions=[
                    DimensionProbe(dimension="d1", probe_prompt="test"),
                    DimensionProbe(dimension="d2", probe_prompt="test"),
                    DimensionProbe(dimension="d3", probe_prompt="test"),
                ],
            )
            for c in data.capabilities
        ]


class FakeExecutor:
    async def execute(self, worker_id: str, probes: list[CapabilityProbes]) -> list[DimensionResult]:
        results = []
        for cp in probes:
            for dim in cp.dimensions:
                results.append(
                    DimensionResult(
                        capability_name=cp.capability_name,
                        dimension=dim.dimension,
                        probe_prompt=dim.probe_prompt,
                        response_content="ok",
                        failed=False,
                    )
                )
        return results


class FakeJudge:
    def __init__(self, confidence: float = 0.9) -> None:
        self._confidence = confidence

    async def judge(self, data: VerifyData, results: list[DimensionResult]) -> list[DimensionJudgment]:
        return [
            DimensionJudgment(
                capability_name=r.capability_name,
                dimension=r.dimension,
                confidence=self._confidence,
            )
            for r in results
        ]


class FakeWorkerRepo:
    def __init__(self) -> None:
        self._workers: dict[str, Worker] = {}
        self._trust_levels: dict[str, TrustLevel] = {}

    def get_by_id(self, worker_id: str) -> Worker | None:
        return self._workers.get(worker_id)

    def update_trust_level(self, worker_id: str, trust_level: TrustLevel) -> Worker:
        self._trust_levels[worker_id] = trust_level
        worker = self._workers.get(worker_id)
        if worker:
            from src.domain.models.worker import WorkerState
            worker = Worker(
                **{**worker.model_dump(), "state": WorkerState(
                    availability=worker.state.availability,
                    trust_level=trust_level,
                    current_load=worker.state.current_load,
                    last_seen_at=worker.state.last_seen_at,
                    runtime_state=worker.state.runtime_state,
                )}
            )
            self._workers[worker_id] = worker
        return worker


class FakeProfileRepo:
    def get_by_id(self, key: str) -> None:
        return None


def _make_worker(with_capabilities: bool = True, trust_level: TrustLevel = TrustLevel.UNVERIFIED) -> Worker:
    caps = [Capability(name="coding", level=CapabilityLevel.ADVANCED)] if with_capabilities else []
    return Worker(
        id="w1",
        type="bot",
        identity={"name": "test", "handle": "@w1", "description": ""},
        responsibilities=[],
        domains=[],
        capabilities=caps,
        skills=[],
        resources=[],
        state={"availability": "public", "trust_level": trust_level.value},
        active_profile_key="",
    )


class TestCapabilityVerifyServiceMapTrustLevel:
    def setup_method(self) -> None:
        self.service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudge(),
            worker_repo=FakeWorkerRepo(),
            profile_repo=FakeProfileRepo(),
        )

    def test_empty_judgments_returns_sandbox_only(self) -> None:
        assert self.service._map_trust_level([]) == TrustLevel.SANDBOX_ONLY

    def test_high_confidence_returns_trusted(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.85)]
        assert self.service._map_trust_level(judgments) == TrustLevel.TRUSTED

    def test_medium_confidence_returns_guarded(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.55)]
        assert self.service._map_trust_level(judgments) == TrustLevel.GUARDED

    def test_low_confidence_returns_sandbox_only(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.3)]
        assert self.service._map_trust_level(judgments) == TrustLevel.SANDBOX_ONLY

    def test_boundary_0_8_returns_trusted(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.8)]
        assert self.service._map_trust_level(judgments) == TrustLevel.TRUSTED

    def test_boundary_0_5_returns_guarded(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.5)]
        assert self.service._map_trust_level(judgments) == TrustLevel.GUARDED

    def test_just_below_0_8_returns_guarded(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.79)]
        assert self.service._map_trust_level(judgments) == TrustLevel.GUARDED

    def test_just_below_0_5_returns_sandbox(self) -> None:
        judgments = [DimensionJudgment(capability_name="c", dimension="d", confidence=0.49)]
        assert self.service._map_trust_level(judgments) == TrustLevel.SANDBOX_ONLY

    def test_multi_domain_averaging(self) -> None:
        # domain1: avg(0.9, 0.9, 0.9) = 0.9; domain2: avg(0.6, 0.6, 0.6) = 0.6
        # overall: (0.9 + 0.6) / 2 = 0.75 → guarded
        judgments = [
            DimensionJudgment(capability_name="coding", dimension="d1", confidence=0.9),
            DimensionJudgment(capability_name="coding", dimension="d2", confidence=0.9),
            DimensionJudgment(capability_name="coding", dimension="d3", confidence=0.9),
            DimensionJudgment(capability_name="writing", dimension="d1", confidence=0.6),
            DimensionJudgment(capability_name="writing", dimension="d2", confidence=0.6),
            DimensionJudgment(capability_name="writing", dimension="d3", confidence=0.6),
        ]
        assert self.service._map_trust_level(judgments) == TrustLevel.GUARDED

    def test_multi_domain_trusted(self) -> None:
        # domain1: 0.9; domain2: 0.85; overall: 0.875 → trusted
        judgments = [
            DimensionJudgment(capability_name="coding", dimension="d1", confidence=0.9),
            DimensionJudgment(capability_name="writing", dimension="d1", confidence=0.85),
        ]
        assert self.service._map_trust_level(judgments) == TrustLevel.TRUSTED


class TestCapabilityVerifyServiceVerify:
    @pytest.mark.asyncio
    async def test_verify_updates_trust_level(self) -> None:
        repo = FakeWorkerRepo()
        worker = _make_worker()
        repo._workers["w1"] = worker

        service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudge(confidence=0.9),
            worker_repo=repo,
            profile_repo=FakeProfileRepo(),
        )

        event = WorkerProfileCreatedEvent(worker_id="w1")
        await service.on_worker_profile_created(event)
        assert repo._trust_levels.get("w1") == TrustLevel.TRUSTED

    @pytest.mark.asyncio
    async def test_skip_nonexistent_worker(self) -> None:
        repo = FakeWorkerRepo()
        service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudge(),
            worker_repo=repo,
            profile_repo=FakeProfileRepo(),
        )
        event = WorkerProfileCreatedEvent(worker_id="missing")
        await service.on_worker_profile_created(event)
        assert "missing" not in repo._trust_levels

    @pytest.mark.asyncio
    async def test_skip_zero_capabilities(self) -> None:
        repo = FakeWorkerRepo()
        worker = _make_worker(with_capabilities=False)
        repo._workers["w1"] = worker

        service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudge(),
            worker_repo=repo,
            profile_repo=FakeProfileRepo(),
        )
        event = WorkerProfileCreatedEvent(worker_id="w1")
        await service.on_worker_profile_created(event)
        assert "w1" not in repo._trust_levels

    @pytest.mark.asyncio
    async def test_skip_already_verified(self) -> None:
        repo = FakeWorkerRepo()
        worker = _make_worker(trust_level=TrustLevel.TRUSTED)
        repo._workers["w1"] = worker

        service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudge(),
            worker_repo=repo,
            profile_repo=FakeProfileRepo(),
        )
        event = WorkerProfileCreatedEvent(worker_id="w1")
        await service.on_worker_profile_created(event)
        # Should NOT update — only UNVERIFIED triggers verification
        assert "w1" not in repo._trust_levels

    @pytest.mark.asyncio
    async def test_timeout_keeps_unverified(self) -> None:
        import asyncio

        class SlowExecutor:
            async def execute(self, worker_id, probes):
                await asyncio.sleep(999)
                return []

        repo = FakeWorkerRepo()
        worker = _make_worker()
        repo._workers["w1"] = worker

        service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=SlowExecutor(),
            judge=FakeJudge(),
            worker_repo=repo,
            profile_repo=FakeProfileRepo(),
            total_timeout=1,  # 1 second timeout
        )
        event = WorkerProfileCreatedEvent(worker_id="w1")
        await service.on_worker_profile_created(event)
        # Should not have updated trust level (timeout)
        assert "w1" not in repo._trust_levels


class FakeLLMGateway:
    """Fake LLM gateway that returns configurable JSON responses."""

    def __init__(self, response_text: str = '{"is_generic": false, "reasoning": "test"}') -> None:
        self._response_text = response_text

    def generate(self, request):
        class FakeResponse:
            raw_text = self._response_text
        return FakeResponse()


class FakeJudgeWithLLM(FakeJudge):
    """FakeJudge that exposes _llm for _check_generic_intro."""

    def __init__(self, confidence: float = 0.9, llm: FakeLLMGateway | None = None) -> None:
        super().__init__(confidence=confidence)
        self._llm = llm or FakeLLMGateway()


class TestCheckGenericIntro:
    """Test _check_generic_intro method."""

    def setup_method(self) -> None:
        self.llm = FakeLLMGateway()
        self.service = CapabilityVerifyService(
            prompt_composer=FakePromptComposer(),
            executor=FakeExecutor(),
            judge=FakeJudgeWithLLM(llm=self.llm),
            worker_repo=FakeWorkerRepo(),
            profile_repo=FakeProfileRepo(),
        )

    @pytest.mark.asyncio
    async def test_generic_intro_returns_true(self) -> None:
        self.llm._response_text = '{"is_generic": true, "reasoning": "仅使用标准 Skill"}'
        result = await self.service._check_generic_intro("我只使用平台标准bash和browser能力")
        assert result["is_generic"] is True
        assert result["reasoning"] == "仅使用标准 Skill"

    @pytest.mark.asyncio
    async def test_custom_skill_intro_returns_false(self) -> None:
        self.llm._response_text = '{"is_generic": false, "reasoning": "加载了自定义 MCP 工具"}'
        result = await self.service._check_generic_intro("我加载了私有MCP工具和工作流")
        assert result["is_generic"] is False
        assert "MCP" in result["reasoning"]

    @pytest.mark.asyncio
    async def test_invalid_json_returns_false(self) -> None:
        self.llm._response_text = "not json at all"
        result = await self.service._check_generic_intro("some intro")
        assert result["is_generic"] is False

    @pytest.mark.asyncio
    async def test_empty_response_returns_false(self) -> None:
        self.llm._response_text = ""
        result = await self.service._check_generic_intro("some intro")
        assert result["is_generic"] is False

    @pytest.mark.asyncio
    async def test_json_with_fence_returns_true(self) -> None:
        self.llm._response_text = '```json\n{"is_generic": true, "reasoning": "纯标准Skill"}\n```'
        result = await self.service._check_generic_intro("标准能力")
        assert result["is_generic"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self) -> None:
        class BrokenLLM:
            def generate(self, request):
                raise RuntimeError("LLM unavailable")
        self.service._judge._llm = BrokenLLM()
        result = await self.service._check_generic_intro("some intro")
        assert result["is_generic"] is False