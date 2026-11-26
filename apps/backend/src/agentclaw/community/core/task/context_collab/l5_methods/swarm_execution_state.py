"""Pure execution revision selection and clarification/session projections."""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


def session_ref(binding_id, stage_id):
    return f"swarm:{binding_id}:{stage_id}"


def current_plan(plans):
    if not plans:
        return None
    by_id = {p.swarm_plan_id: p for p in plans}
    superseded = set()
    for plan in plans:
        previous = plan.termination_condition.get("execution_revision", {}).get(
            "supersedes_plan_ref"
        )
        if previous:
            if previous not in by_id or previous == plan.swarm_plan_id:
                raise ContextCollabValidationError(
                    "broken Swarm execution revision chain"
                )
            superseded.add(previous)
    tips = [p for p in plans if p.swarm_plan_id not in superseded]
    if len(tips) != 1 or len(superseded) != len(plans) - 1:
        raise ContextCollabValidationError("ambiguous Swarm plans")
    seen, cursor = set(), tips[0]
    while cursor.swarm_plan_id not in seen:
        seen.add(cursor.swarm_plan_id)
        previous = cursor.termination_condition.get("execution_revision", {}).get(
            "supersedes_plan_ref"
        )
        if not previous:
            break
        cursor = by_id[previous]
    if len(seen) != len(plans):
        raise ContextCollabValidationError("broken Swarm execution revision chain")
    return tips[0]


def session_events(events, binding_id, stage_id):
    return [
        e
        for e in events
        if e.event_kind.startswith("swarm.")
        and (e.payload or {}).get("binding_id") == binding_id
        and (e.payload or {}).get("stage_ref") == stage_id
    ]


def pending_clarifications(events, binding_id, stage_id):
    relevant = session_events(events, binding_id, stage_id)
    replied = {
        e.payload["clarification_ref"]
        for e in relevant
        if e.event_kind == "swarm.clarification.replied"
    }
    return [
        e
        for e in relevant
        if e.event_kind == "swarm.clarification.requested" and e.event_id not in replied
    ]


def pending_interactions(events, binding_id):
    relevant = [e for e in events if (e.payload or {}).get("binding_id") == binding_id]
    applied = {
        e.payload["request_id"]
        for e in relevant
        if e.event_kind == "swarm.interaction.applied"
    }
    return [
        e
        for e in relevant
        if e.event_kind == "swarm.interaction.posted"
        and e.payload["request_id"] not in applied
    ]
