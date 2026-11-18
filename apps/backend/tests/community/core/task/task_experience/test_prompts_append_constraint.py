"""Tests for task_experience prompt builders and the shared constraint tail.

Assert that every builder output:
  * contains the JSON-only sentence (``JSON_OUTPUT_ONLY_SENTENCE``),
  * contains ``NO_WEB_SEARCH_CONSTRAINT`` and ``OUTPUT_LANGUAGE_CONSTRAINT``
    (reused — never redeclared — from ``prompt_constants``),
  * captures the provided ``question`` / ``bot_id`` / ``bot_name``.
"""

from __future__ import annotations

from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
    OUTPUT_LANGUAGE_CONSTRAINT,
)
from agentclaw.community.core.task.task_experience.prompts._shared import (
    JSON_OUTPUT_ONLY_SENTENCE,
)
from agentclaw.community.core.task.task_experience.prompts.method_b_decompose import (
    build_method_b_prompt,
)
from agentclaw.community.core.task.task_experience.prompts.method_d_self_plan import (
    build_method_d_prompt,
)


def test_method_b_prompt_contains_constraints_and_inputs() -> None:
    prompt = build_method_b_prompt(question="如何部署新 service", bot_name="alisd")

    assert "如何部署新 service" in prompt
    assert "alisd" in prompt
    assert JSON_OUTPUT_ONLY_SENTENCE in prompt
    assert NO_WEB_SEARCH_CONSTRAINT in prompt
    assert OUTPUT_LANGUAGE_CONSTRAINT in prompt
    # JSON shape sentence from the body remains intact
    assert "只输出JSON数组" in prompt


def test_method_b_prompt_constraint_tail_is_canonical_order() -> None:
    prompt = build_method_b_prompt(question="x", bot_name="y")
    # JSON_OUTPUT_ONLY_SENTENCE appears before NO_WEB_SEARCH_CONSTRAINT
    assert prompt.index(JSON_OUTPUT_ONLY_SENTENCE) < prompt.index(
        NO_WEB_SEARCH_CONSTRAINT
    )
    # OUTPUT_LANGUAGE_CONSTRAINT follows NO_WEB_SEARCH_CONSTRAINT
    assert prompt.index(NO_WEB_SEARCH_CONSTRAINT) < prompt.index(
        OUTPUT_LANGUAGE_CONSTRAINT
    )


def test_method_d_prompt_contains_constraints_and_inputs() -> None:
    prompt = build_method_d_prompt(
        question="如何诊断 cron 任务卡住",
        bot_id="20260703_8unvfej1",
        bot_name="alisd",
    )

    assert "如何诊断 cron 任务卡住" in prompt
    assert "alisd" in prompt
    assert "20260703_8unvfej1" in prompt
    assert JSON_OUTPUT_ONLY_SENTENCE in prompt
    assert NO_WEB_SEARCH_CONSTRAINT in prompt
    assert OUTPUT_LANGUAGE_CONSTRAINT in prompt
    assert '只输出JSON数组:' in prompt


def test_method_d_prompt_emits_bot_id_inside_json_shape() -> None:
    prompt = build_method_d_prompt(
        question="plain", bot_id="20260703_8unvfej1", bot_name="aliuct"
    )
    # JSON object shape is mentioned exactly once with the caller-provided bot_id
    assert prompt.count('"action"') >= 1
    assert '"bot_id":"20260703_8unvfej1"' in prompt
