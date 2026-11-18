"""User-prompt builder for method B: LLM decompose + KGBase success_pattern backfill.

The LLM call produces an ordered list of step descriptions; the service then
looks up each step against KGBase ``success_pattern`` and ``capability_boundary``
experience policies to attach . The builder
emits only the user content; the small system prompt is owned by the service.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.prompts._shared import (
    append_constraints,
)

__all__ = ["build_method_b_prompt"]


def build_method_b_prompt(*, question: str, bot_name: str) -> str:
    """Return the user-side prompt asking the model to decompose the question.

    The body explicitly states the JSON array shape (mirroring the prototype)
    so the model output is stable to parse; the mandatory
    ``JSON_OUTPUT_ONLY_SENTENCE`` and cross-cutting constraints are appended by
    :func:`append_constraints`.
    """
    body = (
        "将以下用户问题拆解为执行步骤列表(2-6步),每步用一个简短描述。\n"
        '只输出JSON数组: ["步骤1描述", "步骤2描述", ...]\n\n'
        f"用户问题: {question}\n"
        f"当前bot: {bot_name}\n"
    )
    return append_constraints(body)
