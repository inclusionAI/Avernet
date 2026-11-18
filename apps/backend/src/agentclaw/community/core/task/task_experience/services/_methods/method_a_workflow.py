"""Method A: multibot_workflow direct reuse from KGBase experience.

Lookup a single active ``multibot_workflow`` policy whose trigger matches the
question for the requested ``bot_id``; if found, expand its
``collaboration_chain`` into a 2-segment plan (driver bot gets ~10% of the
trace duration, each downstream chain bot gets the remaining 90% split
evenly). Returns an empty plan with ``reason="kgbase_disabled"`` /
``reason="method_a_no_experience"`` when retrieval is unavailable / empty.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _build_plan,
    _empty_plan,
    _safe_chain,
    _safe_float,
)
from agentclaw.community.core.task.task_experience.types import Plan

__all__ = ["method_a_workflow"]

_DRIVER_ACTION_FALLBACK = "识别问题并创建协作会话"


async def method_a_workflow(
    *,
    question: str,
    bot_id: str,
    policy: PolicyRetrievalPort,
    bot_tags: BotTagsPort,
) -> Plan:
    if policy is None:
        return _empty_plan("A", reason="kgbase_disabled")
    try:
        results = await policy.retrieve(
            question,
            bot_id=bot_id,
            entity_type="Policy",
            property_name="trigger",
            top_k=3,
            min_score=0.3,
            experience_type="multibot_workflow",
            active_only=True,
        )
    except Exception:
        return _empty_plan("A", reason="kgbase_disabled")
    if not results:
        return _empty_plan("A", reason="method_a_no_experience")

    workflow_policy = results[0]
    props = dict(workflow_policy.properties)
    chain = _safe_chain(props.get("collaboration_chain"))
    gain = _safe_float(props.get("gain", 0))
    duration = _safe_float(props.get("trace_duration", 0))
    boundary = str(props.get("boundary", _DRIVER_ACTION_FALLBACK))[:200]
    policy_id = workflow_policy.id

    task_dicts: list[dict] = [
        {
            "action": boundary,
            "bot_id": bot_id,
            "duration": round(duration * 0.1),
            "gain": gain,
            "policy_type": "multibot_workflow",
            "policy_id": policy_id,
        }
    ]
    downstream = chain[1:] if len(chain) >= 2 else []
    if downstream:
        per_bot_duration = round(duration * 0.9 / max(len(downstream), 1))
        for bid in downstream:
            task_dicts.append(
                {
                    "action": f"承接协作请求处理 ({bot_tags.bot_name(bid)})",
                    "bot_id": bid,
                    "duration": per_bot_duration,
                    "gain": gain,
                    "policy_type": "multibot_workflow",
                    "policy_id": policy_id,
                }
            )
    return _build_plan("A", task_dicts, has_policy=True)
