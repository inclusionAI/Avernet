"""VerifyJudge 单元测试。"""

from __future__ import annotations

import json
import pytest

from src.application.services.verify_judge import VerifyJudge
from src.domain.models.verify_dto import (
    CapabilityProbes,
    DimensionJudgment,
    DimensionProbe,
    DimensionResult,
    VerifyData,
)
from src.domain.models.worker import Capability, CapabilityLevel


class FakeLLMProvider:
    def __init__(self, output: str) -> None:
        self._output = output
        self.calls: list[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._output


def _make_verify_data() -> VerifyData:
    return VerifyData(
        worker_id="w1",
        capabilities=[
            Capability(name="coding", level=CapabilityLevel.ADVANCED),
        ],
        soul_md="",
        skill_sets=[],
    )


def _make_result(failed: bool = False, response: str = "I can code") -> DimensionResult:
    return DimensionResult(
        capability_name="coding",
        dimension="syntax",
        probe_prompt="Write a decorator",
        response_content=response,
        failed=failed,
    )


VALID_JUDGE_OUTPUT = json.dumps({
    "dimension": "syntax",
    "confidence": 0.85,
    "reasoning": "Correct and detailed",
})


class TestVerifyJudgeJudge:
    @pytest.mark.asyncio
    async def test_judge_returns_judgments(self) -> None:
        provider = FakeLLMProvider(VALID_JUDGE_OUTPUT)
        judge = VerifyJudge(llm_provider=provider)
        data = _make_verify_data()
        results = [_make_result()]
        judgments = await judge.judge(data, results)
        assert len(judgments) == 1
        assert judgments[0].confidence == 0.85
        assert judgments[0].capability_name == "coding"

    @pytest.mark.asyncio
    async def test_failed_result_gets_zero_confidence(self) -> None:
        provider = FakeLLMProvider("")
        judge = VerifyJudge(llm_provider=provider)
        data = _make_verify_data()
        results = [_make_result(failed=True)]
        judgments = await judge.judge(data, results)
        assert judgments[0].confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_failure_gives_zero_confidence(self) -> None:
        provider = FakeLLMProvider("not valid json")
        judge = VerifyJudge(llm_provider=provider)
        data = _make_verify_data()
        results = [_make_result()]
        judgments = await judge.judge(data, results)
        assert judgments[0].confidence == 0.0


class TestVerifyJudgeConfidenceClamp:
    def test_clamp_high_confidence(self) -> None:
        provider = FakeLLMProvider("")
        judge = VerifyJudge(llm_provider=provider)
        output = json.dumps({"dimension": "x", "confidence": 1.5, "reasoning": ""})
        result = judge._parse_output("coding", output)
        assert result.confidence == 1.0

    def test_clamp_negative_confidence(self) -> None:
        provider = FakeLLMProvider("")
        judge = VerifyJudge(llm_provider=provider)
        output = json.dumps({"dimension": "x", "confidence": -0.3, "reasoning": ""})
        result = judge._parse_output("coding", output)
        assert result.confidence == 0.0

    def test_clamp_normal_confidence_unchanged(self) -> None:
        provider = FakeLLMProvider("")
        judge = VerifyJudge(llm_provider=provider)
        output = json.dumps({"dimension": "x", "confidence": 0.65, "reasoning": ""})
        result = judge._parse_output("coding", output)
        assert result.confidence == 0.65


class TestVerifyJudgeParseOutput:
    def test_parse_with_code_fence(self) -> None:
        provider = FakeLLMProvider("")
        judge = VerifyJudge(llm_provider=provider)
        fenced = f"```json\n{VALID_JUDGE_OUTPUT}\n```"
        result = judge._parse_output("coding", fenced)
        assert result.confidence == 0.85