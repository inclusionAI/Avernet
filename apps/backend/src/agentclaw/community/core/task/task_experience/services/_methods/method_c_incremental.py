"""Method C: incremental rewrite + full-policy retrieval (KGBase only).

Walks a task queue up to ``max_rounds`` deep, querying KGBase for
``capability_boundary`` / ``success_pattern`` / ``multibot_workflow`` policies
for each step. Matched policies append coverage tasks with the
policy-derived ``gain`` / ``trace_duration``; unmatched steps become
``policy_type=None`` placeholders. The shape mirrors the prototype's
``_method_c_incremental`` so plans are reviewable side-by-side.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_experience.protocols import (
    BotTagsPort,
    PolicyRetrievalPort,
)
from agentclaw.community.core.task.task_experience.services._methods._common import (
    _build_plan,
    _empty_plan,
    _retrieve_for,
    _safe_chain,
    _safe_float,
)
from agentclaw.community.core.task.task_experience.types import Plan

__all__ = ["method_c_incremental"]

_MAX_ROUNDS_DEFAULT = 5


async def method_c_incremental(
    *,
    question: str,
    bot_id: str,
    policy: PolicyRetrievalPort,
    bot_tags: BotTagsPort,
    max_rounds: int = _MAX_ROUNDS_DEFAULT,
) -> Plan:
    if policy is None:
        return _empty_plan("C", reason="kgbase_disabled")

    covered: list[dict] = []
    remaining: list[dict] = [{"action": question, "bot_id": bot_id}]
    rounds = 0

    while remaining and rounds < max_rounds:
        rounds += 1
        current = remaining.pop(0)
        action_text = current["action"]
        cur_bot_id = current.get("bot_id", bot_id)

        cap = await _retrieve_for(
            policy, action_text=action_text, bot_id=bot_id,
            experience_type="capability_boundary",
        )
        sp = await _retrieve_for(
            policy, action_text=action_text, bot_id=bot_id,
            experience_type="success_pattern",
        )
        wf = await _retrieve_for(
            policy, action_text=action_text, bot_id=bot_id,
            experience_type="multibot_workflow",
        )

        placed = False

        if cap:
            p = cap[0]
            props = dict(p.properties)
            target = str(props.get("target_bot_id") or props.get("target") or "")
            covered.append(
                {
                    "action": action_text,
                    "bot_id": bot_id,
                    "duration": _safe_float(props.get("trace_duration", 0)) * 0.3,
                    "gain": _safe_float(props.get("gain", 0)),
                    "policy_type": "capability_boundary",
                    "policy_id": p.id,
                }
            )
            if target and target != bot_id:
                remaining.append(
                    {
                        "action": f"由{bot_tags.bot_name(target)}处理: {action_text}",
                        "bot_id": target,
                    }
                )
            placed = True

        if sp:
            p = sp[0]
            props = dict(p.properties)
            covered.append(
                {
                    "action": action_text,
                    "bot_id": bot_id,
                    "duration": _safe_float(props.get("trace_duration", 0)),
                    "gain": _safe_float(props.get("gain", 0)),
                    "policy_type": "success_pattern",
                    "policy_id": p.id,
                }
            )
            placed = True

        if wf:
            p = wf[0]
            props = dict(p.properties)
            chain = _safe_chain(props.get("collaboration_chain"))
            dur = _safe_float(props.get("trace_duration", 0))
            gain = _safe_float(props.get("gain", 0))
            downstream = chain[1:] if len(chain) >= 2 else []
            per_bot_duration = (
                round(dur * 0.9 / max(len(downstream), 1)) if downstream else 0
            )
            for bid in downstream:
                covered.append(
                    {
                        "action": f"{bot_tags.bot_name(bid)}承接: {action_text}",
                        "bot_id": bid,
                        "duration": per_bot_duration,
                        "gain": gain,
                        "policy_type": "multibot_workflow",
                        "policy_id": p.id,
                    }
                )
            placed = True

        if not placed:
            covered.append(
                {
                    "action": action_text,
                    "bot_id": cur_bot_id,
                    "duration": 0,
                    "gain": 0.0,
                    "policy_type": None,
                    "policy_id": None,
                }
            )

    has_policy = any(bool(t.get("policy_id")) for t in covered)
    return _build_plan("C", covered, has_policy=has_policy)
