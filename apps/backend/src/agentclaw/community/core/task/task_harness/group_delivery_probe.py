"""Observe manager-worker deliveries without confusing group and run lifetimes."""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.client.ports import BcsClientPort
from agentclaw.community.core.task.task_harness.runtime_probe import (
    RuntimeLivenessState, RuntimeProbeResult, _digest, _last_message, _last_message_cursor, _message_sort_key,
)


async def probe_group_deliveries(
    bcs: BcsClientPort, node: TaskNode, *, messages: list[Any], caller_bearer: str | None,
    driver: str, previous_cursor: str | None,
) -> RuntimeProbeResult:
    """Only the coordinator's latest message delivery can authorize recovery.

    History includes pending inputs so an admitted but not yet running resume
    fences an old failure. An unresolvable/trimmed history is not failure proof.
    Worker failures and arbitrary error text never fail the coordinator.
    """
    props = node.run_info.extend_props or {}
    session_id = str(props.get("session_id") or "")
    source = "bcs.delivery"

    def unknown(reason: str) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            state="activity_unknown", source=source, detail={"reason": reason},
        )

    if not driver or not isinstance(messages, list):
        return unknown("missing_coordinator_history")
    records = [message for message in messages if isinstance(message, dict)]
    resume_message = str(props.get("relay_harness_delivery_message_id")
                         or props.get("relay_harness_resume_message_id") or "")
    rows = []
    queried = set()
    page = records
    # Locate a trimmed initial request only until its source is known. Polling
    # then uses the persisted association instead of rereading the whole history.
    for page_index in range(8):
        by_id = {str(m.get("id") or m.get("message_id") or ""): m for m in records}
        by_id.pop("", None)
        source_ids = [key for key, message in by_id.items()
                      if not message.get("run_id") or message.get("role") == "user"]
        if resume_message and resume_message not in source_ids:
            source_ids.append(resume_message)
        ids = [key for key in source_ids if key not in queried]
        for offset in range(0, len(ids), 100):
            batch = ids[offset:offset + 100]
            result = await bcs.query_message_deliveries(
                session_id, message_ids=batch, caller_bearer=caller_bearer,
            )
            if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
                return unknown("invalid_delivery_response")
            if any(row.get("message_id") not in batch for row in result):
                return unknown("invalid_delivery_identity")
            rows.extend(result)
            queried.update(batch)
        deliveries = [row for row in rows if row.get("target_bot_id") == driver and row.get("kind") == "send"]
        if deliveries or len(page) < 100 or page_index == 7:
            break
        timestamps = [message["timestamp"] for message in page if type(message.get("timestamp")) is int]
        if not timestamps:
            break
        before = min(timestamps)
        page = await bcs.get_session_messages(
            session_id, limit=100, before=before, caller_bearer=caller_bearer,
            include_pending=False,
        )
        if not isinstance(page, list) or not page:
            break
        page = [message for message in page if isinstance(message, dict)
                and type(message.get("timestamp")) is int and message["timestamp"] < before]
        if not page:
            break
        records.extend(page)
    if not queried:
        return unknown("missing_delivery_source")
    if not deliveries:
        return unknown("missing_coordinator_delivery")
    if any(not row.get("run_id") or not row.get("delivery_id") or row.get("message_id") not in queried
           for row in deliveries):
        return unknown("invalid_delivery_identity")

    # With a retained source, timestamp/sequence orders execution requests, not
    # the arrival order of terminal callbacks. An admitted resume whose source
    # fell out of history is still a fence; never fall back to the old run.
    visible_deliveries = [row for row in deliveries if row["message_id"] in by_id]
    if resume_message and resume_message not in by_id:
        # The cached current request may have been trimmed while old inputs
        # remain visible. Never let those old inputs supersede its admission.
        deliveries = [row for row in deliveries if row["message_id"] == resume_message]
    elif visible_deliveries:
        latest_source = _last_message([by_id[row["message_id"]] for row in visible_deliveries])
        source_id = str(latest_source.get("id") or latest_source.get("message_id"))
        deliveries = [row for row in visible_deliveries if row["message_id"] == source_id]
    else:
        deliveries = [row for row in deliveries if row["message_id"] == resume_message]
    if len(deliveries) != 1:
        return unknown("ambiguous_coordinator_delivery")
    delivery = deliveries[0]
    selected_source = by_id.get(str(delivery["message_id"]))
    if any(
        not message.get("run_id")
        and (message.get("message_type") in {"chat", "system"} or message.get("role") == "user")
        and (selected_source is None or
             _message_sort_key((0, message)) > _message_sort_key((0, selected_source)))
        for message in records
    ):
        return unknown("newer_input_not_yet_observable")
    run_id = str(delivery["run_id"])
    run_messages = [m for m in records if m.get("run_id") and
                    (m.get("sender") or m.get("sender_id")) == driver]
    latest_run_message = _last_message(run_messages)
    if latest_run_message and str(latest_run_message["run_id"]) != run_id:
        # A different run may already have started, including an out-of-window
        # source. It must not be recovered as if the old failed run were current.
        latest_source = by_id.get(str(delivery["message_id"]))
        if latest_source is None or _message_sort_key((0, latest_run_message)) > _message_sort_key((0, latest_source)):
            return unknown("newer_execution_observed")

    status = str(delivery.get("status") or "").lower()
    detail = {"run_id": run_id, "delivery_id": delivery["delivery_id"],
              "message_id": delivery["message_id"], "session_id": session_id}
    # These are admission/wait/uncertainty facts, not a stopped executor. In
    # particular, Unknown must not become a failure after N polling cycles.
    if status in {"queued", "dispatching", "unknown", "cancelling", "cancel_unknown",
                  "pending_context", "bound"}:
        return RuntimeProbeResult(state="activity_unknown", source=source, status=status, detail=detail)
    states: dict[str, RuntimeLivenessState] = {"failed": "terminal_failed", "completed": "terminal_success", "running": "active"}
    if status not in states:
        return unknown("non_recoverable_delivery_state")
    cursor = _digest([run_id, delivery["delivery_id"], status, _last_message_cursor(run_messages)])
    return RuntimeProbeResult(
        state=states[status], source=source, status=status, heartbeat_cursor=cursor,
        progressed=bool(previous_cursor and previous_cursor != cursor), detail=detail,
    )
