"""Relay-specific validation and persistence for independent TopN sampling."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskNodePatch


def build_relay_sample_patch(
    *,
    task_id: str,
    node_id: str,
    payload: dict[str, Any],
    sample_count: int,
    progress_reason: str,
    failure_reason: str | None,
) -> tuple[TaskNodePatch, str, list[str]]:
    """Validate an independent Relay sample decision and build its node patch."""
    if sample_count <= 1:
        raise TaskStateError(
            "HIT_MULTI_SAMPLES requires task_dispatch.sample_count greater than 1"
        )
    raw_samples = payload.get("sample_bot_ids")
    if not isinstance(raw_samples, list):
        raise TaskStateError("HIT_MULTI_SAMPLES requires sample_bot_ids")
    sample_bot_ids = [str(item).strip() for item in raw_samples if str(item).strip()]
    if len(sample_bot_ids) < 2:
        raise TaskStateError("HIT_MULTI_SAMPLES requires at least two sample_bot_ids")
    if len(sample_bot_ids) > sample_count:
        raise TaskStateError(
            "HIT_MULTI_SAMPLES exceeds configured task_dispatch.sample_count"
        )
    if len(set(sample_bot_ids)) != len(sample_bot_ids):
        raise TaskStateError("HIT_MULTI_SAMPLES requires distinct sample_bot_ids")
    driver = str(payload.get("driver_bot_id") or "").strip()
    if driver != sample_bot_ids[0]:
        raise TaskStateError(
            "HIT_MULTI_SAMPLES requires driver_bot_id equal to the first sample_bot_id"
        )
    samples = [
        {"sample_id": f"sample-{rank}", "rank": rank, "bot_id": bot_id}
        for rank, bot_id in enumerate(sample_bot_ids, start=1)
    ]
    return (
        TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            run_mode="single_bot",
            assignee=driver,
            progress_reason=progress_reason,
            failure_reason=failure_reason,
            extend_props_patch={
                "dispatch_samples": samples,
                "relay_holder_id": driver,
                "driver_bot_id": driver,
                "next_relay_bots": sample_bot_ids,
            },
        ),
        driver,
        sample_bot_ids,
    )


def dispatch_bot_ids(payload: dict[str, Any], outcome: str) -> list[str]:
    """Read the Bot list from the outcome-specific Relay payload field."""
    raw = (
        payload.get("sample_bot_ids")
        if outcome == "HIT_MULTI_SAMPLES"
        else payload.get("next_relay_bots") or payload.get("bot_ids")
    )
    return [str(item).strip() for item in (raw or []) if str(item).strip()]


def hit_action(outcome: str) -> str:
    """Map a Relay hit outcome to its trajectory action label."""
    if outcome == "HIT_SINGLE":
        return "hit_single"
    if outcome == "HIT_MULTI_SAMPLES":
        return "hit_multi_sample"
    return "hit_multi"


def decision_response(
    *, node_id: str, driver: str, bot_ids: list[str], outcome: str
) -> dict[str, Any]:
    """Build the additive response without changing legacy hit payloads."""
    result: dict[str, Any] = {
        "ok": True,
        "published_bbs": False,
        "node_id": node_id,
        "driver_bot_id": driver,
        "next_relay_bots": bot_ids,
    }
    if outcome == "HIT_MULTI_SAMPLES":
        result["sample_bot_ids"] = bot_ids
    return result
