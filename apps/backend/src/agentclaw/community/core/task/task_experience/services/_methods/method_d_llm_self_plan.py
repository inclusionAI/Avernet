"""Method D: LLM self-plan (no policy lookup).

Ask the LLM to produce its own 2-5 step plan as a JSON array of
``{action, bot_id}`` objects, keyed by the caller-supplied ``bot_id`` (the
prompt builder embeds it in the JSON shape so the model echoes it back).
Method D does not consult KGBase, so ``has_policy_support`` is always
``False`` — method D is a baseline fallback in the four-plan ranking with no
policy backing.

Plan reasons:
- ``llm_disabled`` — LLM returned the disabled sentinel (no token / base_url).
- ``invalid_model_output`` — chat succeeded but JSON could not be parsed or
  contained no dict-shaped entries; the plan is ``_empty_plan`` per forum's
  sentinel-skip style (mirrors prototype behaviour of the empty-list branch).
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.prompts.method_d_self_plan import (
    build_method_d_prompt,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _build_plan,
    _empty_plan,
    parse_json_steps,
)
from agentclaw.community.core.task.task_experience.types import (
    TASK_EXPERIENCE_LLM_DISABLED,
    Plan,
)

__all__ = ["method_d_llm_self_plan"]

_SYSTEM_PROMPT = "你是一个规划助手。只输出JSON数组。"
_CHAT_MAX_TOKENS = 500


async def method_d_llm_self_plan(
    *,
    question: str,
    bot_id: str,
    llm: TaskExperienceLLMProtocol,
    bot_tags: BotTagsPort,
) -> Plan:
    if llm is None:
        return _empty_plan("D", reason="llm_disabled")
    bot_name = bot_tags.bot_name(bot_id)
    user_prompt = build_method_d_prompt(
        question=question, bot_id=bot_id, bot_name=bot_name,
    )
    try:
        result = await llm.chat(
            system=_SYSTEM_PROMPT, user=user_prompt,
            max_tokens=_CHAT_MAX_TOKENS,
        )
    except Exception:
        return _empty_plan("D", reason="invalid_model_output")

    if result.strip() == TASK_EXPERIENCE_LLM_DISABLED:
        return _empty_plan("D", reason="llm_disabled")

    parsed = parse_json_steps(result)
    if parsed is None:
        return _empty_plan("D", reason="invalid_model_output")

    task_dicts: list[dict] = []
    for item in parsed:
        if isinstance(item, dict):
            task_dicts.append({
                "action": str(item.get("action", "")),
                "bot_id": str(item.get("bot_id", bot_id)),
                "duration": 0.0,
                "gain": 0.0,
                "policy_type": None,
                "policy_id": None,
            })
    if not task_dicts:
        return _empty_plan("D", reason="invalid_model_output")

    return _build_plan("D", task_dicts, has_policy=False)
