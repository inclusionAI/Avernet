"""Conformance suite for ``TaskExperienceLLMProtocol``.

Documented invariants:
  - empty ``base_url`` or ``secret_name`` ⇒ ``chat`` returns the disabled
    sentinel ``TASK_EXPERIENCE_LLM_DISABLED`` without raising
  - missing / blank secret value ⇒ same disabled sentinel
  - happy path: response is trimmed ``choices[0].message.content``
  - ``max_tokens <= 0`` raises ``ValueError`` (validation precedes HTTP gate)
"""

from __future__ import annotations

from typing import Callable

import pytest

from agentclaw.community.core.task.task_experience.protocols import (
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.types import (
    TASK_EXPERIENCE_LLM_DISABLED,
)

__all__ = ["assert_task_experience_llm_invariants", "LLMFactory"]

LLMFactory = Callable[..., TaskExperienceLLMProtocol]


async def assert_task_experience_llm_invariants(make_llm: LLMFactory) -> None:
    """Assert documented ``TaskExperienceLLMProtocol`` invariants.

    ``make_llm`` must accept these kwargs at minimum (extra kwargs ignored):
    ``base_url: str``, ``secret_name: str``, ``secret_token: str | None``,
    optional ``response_content: str`` used for the happy-path stub.
    Returns a fresh :class:`TaskExperienceLLMProtocol` instance.
    """
    no_base = make_llm(base_url="", secret_name="", secret_token="tok")
    assert await no_base.chat(system="s", user="u") == TASK_EXPERIENCE_LLM_DISABLED

    no_secret_name = make_llm(
        base_url="https://antchat.example/v1",
        secret_name="",
        secret_token="tok",
    )
    assert await no_secret_name.chat(system="s", user="u") == TASK_EXPERIENCE_LLM_DISABLED

    no_token = make_llm(
        base_url="https://antchat.example/v1",
        secret_name="task_experience_antllm_key",
        secret_token=None,
    )
    assert await no_token.chat(system="s", user="u") == TASK_EXPERIENCE_LLM_DISABLED

    happy = make_llm(
        base_url="https://antchat.example/v1",
        secret_name="task_experience_antllm_key",
        secret_token="tok",
        response_content="hello world   ",
    )
    assert await happy.chat(system="s", user="u") == "hello world"

    configured = make_llm(
        base_url="https://antchat.example/v1",
        secret_name="task_experience_antllm_key",
        secret_token="tok",
    )
    with pytest.raises(ValueError):
        await configured.chat(system="s", user="u", max_tokens=0)
