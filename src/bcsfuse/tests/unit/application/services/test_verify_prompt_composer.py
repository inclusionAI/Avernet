"""VerifyPromptComposer 单元测试。"""

from __future__ import annotations

import json
import pytest

from src.application.services.verify_prompt_composer import VerifyPromptComposer
from src.domain.models.verify_dto import CapabilityProbes, DimensionProbe, VerifyData
from src.domain.models.worker import Capability, CapabilityLevel


class FakeLLMProvider:
    """Mock LLM Provider。"""

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
        soul_md="I am a coding bot",
        skill_sets=[{"name": "python", "description": "Python development"}],
    )


VALID_LLM_OUTPUT = json.dumps({
    "capability_probes": [
        {
            "capability_name": "coding",
            "dimensions": [
                {"dimension": "syntax", "probe_prompt": "Write a decorator"},
                {"dimension": "algo", "probe_prompt": "Implement BFS"},
                {"dimension": "debug", "probe_prompt": "Find the bug"},
            ],
        }
    ]
})


class TestVerifyPromptComposerCompose:
    @pytest.mark.asyncio
    async def test_compose_returns_probes(self) -> None:
        provider = FakeLLMProvider(VALID_LLM_OUTPUT)
        composer = VerifyPromptComposer(llm_provider=provider)
        result = await composer.compose(_make_verify_data())
        assert len(result) == 1
        assert result[0].capability_name == "coding"
        assert len(result[0].dimensions) == 3

    @pytest.mark.asyncio
    async def test_compose_calls_llm_with_prompt(self) -> None:
        provider = FakeLLMProvider(VALID_LLM_OUTPUT)
        composer = VerifyPromptComposer(llm_provider=provider)
        await composer.compose(_make_verify_data())
        assert len(provider.calls) == 1
        assert "coding" in provider.calls[0]


class TestVerifyPromptComposerParseOutput:
    def test_parse_valid_json(self) -> None:
        provider = FakeLLMProvider("")
        composer = VerifyPromptComposer(llm_provider=provider)
        result = composer._parse_output(VALID_LLM_OUTPUT)
        assert len(result) == 1

    def test_parse_json_with_code_fence(self) -> None:
        fenced = f"```json\n{VALID_LLM_OUTPUT}\n```"
        provider = FakeLLMProvider("")
        composer = VerifyPromptComposer(llm_provider=provider)
        result = composer._parse_output(fenced)
        assert len(result) == 1

    def test_parse_invalid_json_raises(self) -> None:
        provider = FakeLLMProvider("")
        composer = VerifyPromptComposer(llm_provider=provider)
        with pytest.raises(json.JSONDecodeError):
            composer._parse_output("not json")

    @pytest.mark.asyncio
    async def test_compose_llm_failure_raises(self) -> None:
        provider = FakeLLMProvider("bad output")
        composer = VerifyPromptComposer(llm_provider=provider)
        with pytest.raises(Exception):
            await composer.compose(_make_verify_data())