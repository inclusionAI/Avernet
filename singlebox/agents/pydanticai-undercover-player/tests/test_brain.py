from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic_ai import ModelResponse, ToolCallPart, UnexpectedModelBehavior
from pydantic_ai.messages import RetryPromptPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from pydanticai_player.brain import PydanticPlayer
from pydanticai_player.domain import READY_TEXT


@pytest.mark.parametrize("invalid", [None, 2, 99, True, "3"])
async def test_framework_repairs_invalid_vote(invalid, vote_task):
    calls, saw_retry = 0, False

    def model(messages, info):
        nonlocal calls, saw_retry
        calls += 1
        saw_retry |= any(isinstance(p, RetryPromptPart) for m in messages for p in m.parts)
        value = invalid if calls == 1 else 3
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"target": value})])

    result = await PydanticPlayer(FunctionModel(model)).answer(vote_task)
    assert result.text == "我投3号"
    assert calls == result.requests == 2
    assert saw_retry  # PydanticAI, rather than an outer repair loop, owns the retry.


@pytest.mark.parametrize("invalid", ["雨伞很好用。", "下雨时把伞打开。", "长" * 26, "甲\n乙", ""])
async def test_framework_repairs_speech_before_publication(invalid, speech_task):
    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        text = invalid if calls == 1 else "天气变化时会带着。"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"text": text})])

    answer = await PydanticPlayer(FunctionModel(model)).answer(speech_task)
    assert answer.text == "天气变化时会带着。"
    assert answer.requests == 2


async def test_retry_budget_has_no_rule_fallback(vote_task):
    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"target": 2})])

    with pytest.raises(UnexpectedModelBehavior):
        await PydanticPlayer(FunctionModel(model), output_retries=2).answer(vote_task)
    assert calls == 3


async def test_each_run_has_only_current_task_context(vote_task, speech_task):
    prompts = []

    def model(messages, info):
        prompts.append(
            [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
        )
        args = {"text": "天气变化时会带着。"} if len(prompts) == 1 else {"target": 3}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    player = PydanticPlayer(FunctionModel(model))
    await player.answer(replace(speech_task, instruction="SESSION_A_PRIVATE"))
    await player.answer(
        replace(vote_task, instruction="SESSION_B_PRIVATE", upstream="SECRET_SIMULTANEOUS_VOTE")
    )
    assert len(prompts[1]) == 1
    assert "SESSION_A_PRIVATE" not in str(prompts[1])
    assert "SECRET_SIMULTANEOUS_VOTE" not in str(prompts[1])


async def test_real_framework_call_is_cancellable(vote_task):
    started = asyncio.Event()

    async def model(messages, info):
        started.set()
        await asyncio.Event().wait()

    work = asyncio.create_task(PydanticPlayer(FunctionModel(model)).answer(vote_task))
    await asyncio.wait_for(started.wait(), 1)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work


async def test_vote_start_uses_no_model(speech_task):
    def forbidden(messages, info):
        pytest.fail("Readiness acknowledgement must not invoke inference")

    answer = await PydanticPlayer(FunctionModel(forbidden)).answer(
        replace(speech_task, kind="ready"),
    )
    assert answer.text == READY_TEXT and answer.requests == 0


async def test_eulogy_supplies_only_its_session_private_identity_to_model(speech_task):
    def model(messages, info):
        prompts = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
        context = json.loads(prompts[0])
        assert context["private_context"] == {"seat": 2, "word": "雨伞"}
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"text": "祝大家好运。"})]
        )

    task = replace(speech_task, kind="eulogy", instruction="你被投出局了，请说一句遗言。")
    answer = await PydanticPlayer(FunctionModel(model)).answer(task)
    assert answer.text == "祝大家好运。"
