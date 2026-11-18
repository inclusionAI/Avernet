"""Method B: LLM decompose + KGBase success_pattern backfill.

Ask the LLM to produce an ordered list of step descriptions (2-6 steps), then
for each step retrieve KGBase ``success_pattern`` and ``capability_boundary``
policies for the requested ``bot_id`` to attach ``gain`` / ``trace_duration``
and pick the executing bot (cap policy whose ``target_bot_id`` differs from
the caller is preferred when no success_pattern exists). Output mirrors the
prototype's ``_method_b_llm_decompose`` while running on the Protocol ports.

Plan reasons:
- ``llm_disabled`` — LLM returned the disabled sentinel (no token / base_url).
- ``invalid_model_output`` — chat succeeded but JSON could not be parsed / was not a list.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
    TaskExperienceLLMProtocol,
)
from agentclaw.community.core.task.task_experience.prompts.method_b_decompose import (
    build_method_b_prompt,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _build_plan,
    _empty_plan,
    _retrieve_for,
    _safe_float,
    parse_json_steps,
)
from agentclaw.community.core.task.task_experience.types import (
    TASK_EXPERIENCE_LLM_DISABLED,
    Plan,
)

__all__ = ["method_b_llm_decompose"]

_SYSTEM_PROMPT = "你是一个任务拆解助手。只输出JSON数组。"
_CHAT_MAX_TOKENS = 500


async def method_b_llm_decompose(
    *,
    question: str,
    bot_id: str,
    llm: TaskExperienceLLMProtocol,
    policy: PolicyRetrievalPort,
    bot_tags: BotTagsPort,
) -> Plan:
    if llm is None:
        return _empty_plan("B", reason="llm_disabled")
    bot_name = bot_tags.bot_name(bot_id)
    user_prompt = build_method_b_prompt(question=question, bot_name=bot_name)
    try:
        result = await llm.chat(
            system=_SYSTEM_PROMPT, user=user_prompt,
            max_tokens=_CHAT_MAX_TOKENS,
        )
    except Exception:
        return _empty_plan("B", reason="invalid_model_output")

    if result.strip() == TASK_EXPERIENCE_LLM_DISABLED:
        return _empty_plan("B", reason="llm_disabled")

    parsed = parse_json_steps(result)
    if parsed is None:
        return _empty_plan("B", reason="invalid_model_output")
    step_descs = [str(s) for s in parsed]
    if not step_descs:
        return _empty_plan("B", reason="invalid_model_output")

    task_dicts: list[dict] = []
    for step_desc in step_descs:
        sp_list = await _retrieve_for(
            policy, action_text=step_desc, bot_id=bot_id,
            experience_type="success_pattern",
        )
        cap_list = await _retrieve_for(
            policy, action_text=step_desc, bot_id=bot_id,
            experience_type="capability_boundary",
        )
        if sp_list:
            p = sp_list[0]
            props = dict(p.properties)
            task_dicts.append({
                "action": step_desc,
                "bot_id": bot_id,
                "duration": _safe_float(props.get("trace_duration", 0)),
                "gain": _safe_float(props.get("gain", 0)),
                "policy_type": "success_pattern",
                "policy_id": p.id,
            })
        elif cap_list:
            p = cap_list[0]
            props = dict(p.properties)
            target = str(props.get("target_bot_id") or props.get("target") or "")
            task_dicts.append({
                "action": step_desc,
                "bot_id": target or bot_id,
                "duration": _safe_float(props.get("trace_duration", 0)),
                "gain": _safe_float(props.get("gain", 0)),
                "policy_type": "capability_boundary",
                "policy_id": p.id,
            })
        else:
            task_dicts.append({
                "action": step_desc,
                "bot_id": bot_id,
                "duration": 0.0,
                "gain": 0.0,
                "policy_type": None,
                "policy_id": None,
            })

    has_policy = any(bool(t.get("policy_id")) for t in task_dicts)
    return _build_plan("B", task_dicts, has_policy=has_policy)
