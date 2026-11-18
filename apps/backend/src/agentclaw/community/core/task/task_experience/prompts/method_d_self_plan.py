"""User-prompt builder for method D: LLM self-plan (no policy lookup).

The LLM call produces its own 2-5 step plan keyed by ``bot_id``; the service
passes those through untouched. Other than JSON output guidance, method D has
no other shared contract with methods B/C, hence the separate builder module.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.prompts._shared import (
    append_constraints,
)

__all__ = ["build_method_d_prompt"]


def build_method_d_prompt(
    *,
    question: str,
    bot_id: str,
    bot_name: str,
) -> str:
    """Return the user-side prompt asking the model for its own plan.

    ``bot_name`` provides context for the model's voice (``你是 {bot_name}
    助手``); ``bot_id`` is requested as the value of ``bot_id`` in the returned
    steps so the service can attribute the plan without re-resolving.
    """
    body = (
        f"你是 {bot_name} 助手。为以下问题生成执行计划(2-5步)。\n"
        '只输出JSON数组: [{"action":"步骤描述","bot_id":"' + bot_id + '"}, ...]\n\n'
        f"问题: {question}\n"
    )
    return append_constraints(body)
