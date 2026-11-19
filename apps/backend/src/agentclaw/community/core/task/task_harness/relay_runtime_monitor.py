"""Relay-only runtime observation and same-session recovery policy.

Activity is not proof of business completion. Probes observe runtime/messages;
node-local evidence, pending operations and precise continuation belong to Skill.
"""
from __future__ import annotations

import logging
from agentclaw.community.core.task.domain.models import TaskNode, TaskNodePatch
from agentclaw.community.core.task.task_harness import relay_recovery_policy as recovery_policy
from agentclaw.community.core.task.task_harness.relay_runtime_policy import (
    resolve_relay_resume_step, relay_resume_attempts, terminal_success_unreported_seconds,
)
from agentclaw.community.core.task.task_harness.relay_resume_trajectory import emit_relay_resume_trajectory
from agentclaw.community.core.task.task_harness.relay_session_recovery import (
    RelaySessionRecoveryError, RelaySessionPreconditionError,
)
from agentclaw.community.core.task.task_harness.runtime_probe import RuntimeProbeResult

logger = logging.getLogger("task.harness")

async def monitor_relay_runtime(
    self,
    task_id: str,
    node: TaskNode,
    *,
    elapsed: float,
    sla: float,
) -> TaskNodePatch | None:
    """Turn one Relay runtime observation into a harness decision.

    Pure elapsed time is only a duty-cycle trigger here, never failure
    evidence. A node is retried only after authoritative terminal failure,
    terminal success without a task report, or repeated unknown/unresponsive
    probes. Active silence is measured with monotonic elapsed time; it triggers
    same-session reconciliation, not task failure or business replay.
    """
    if self._runtime_probe is None:
        return None
    key = (task_id, node.node_id)
    current = self._runtime_seen.setdefault(
        key,
        {
            "cursor": None,
            "unknown": 0,
            "no_progress": 0,
            "success_unreported": 0,
            "resume_attempts": 0,
            "resume_send_failures": 0,
            "resume_precondition_failures": 0,
            "probe_unavailable": 0,
            "terminal_success_observed_at": None,
        },
    )
    try:
        result = await self._runtime_probe.probe(
            node, previous_cursor=current.get("cursor")
        )
    except Exception as exc:  # noqa: BLE001 - probing must never break harness
        logger.warning(
            "[task][harness] runtime probe failed task=%s node=%s error=%s",
            task_id, node.node_id, str(exc)[:500], exc_info=True,
        )
        result = RuntimeProbeResult(
            state="unknown", error=f"{type(exc).__name__}: {exc}"[:500]
        )

    if result.state != "probe_unavailable":
        # Successful/observable probes update the cursor; an unavailable probe
        # has no observation and therefore must not overwrite the last known
        # cursor. Otherwise the next successful probe could be misjudged as
        # fresh progress after service instability.
        current["cursor"] = result.heartbeat_cursor
    resume_step = resolve_relay_resume_step(task_id, node, self._graph)
    attempts = relay_resume_attempts(node, current)
    now = self._clock()
    if now < current.get("resume_quiet_until", 0.0):
        return None
    terminal_success_elapsed = 0.0
    if current.get("terminal_success_observed_at") is not None:
        terminal_success_elapsed = max(0.0, now - float(current["terminal_success_observed_at"]))
    detail = {
        "runtime_state": result.state,
        "runtime_status": result.status,
        "runtime_source": result.source,
        "runtime_error": result.error,
        "resume_step": resume_step,
        "terminal_success_elapsed_seconds": terminal_success_elapsed,
    }
    reason: str | None = None

    if result.state == "terminal_failed":
        current["terminal_success_observed_at"] = None
        reason = "runtime_failed"
    elif result.state == "terminal_success":
        if current.get("terminal_success_observed_at") is None:
            # Start the observation clock from the first runtime success
            # observation. Time spent earlier in the engine's active state
            # must not silently consume the silent-success recovery window.
            current["terminal_success_observed_at"] = now
        terminal_success_elapsed = max(0.0, now - float(current["terminal_success_observed_at"]))
        current["success_unreported"] += 1
        current["unknown"] = 0
        current["no_progress"] = 0
        if terminal_success_elapsed >= terminal_success_unreported_seconds(attempts):
            reason = "terminal_success_unreported"
    elif result.state == "active":
        current["success_unreported"] = 0
        current["terminal_success_observed_at"] = None
        current["unknown"] = 0
        if result.progressed:
            current["no_progress"] = 0
            current["last_activity_at"] = now
        else:
            # Repeated silence means the runtime turn cannot self-advance.
            current["no_progress"] += 1
            last_activity = current.setdefault("last_activity_at", now)
            stagnant_seconds = max(0.0, now - last_activity)
            detail["stagnant_seconds"] = stagnant_seconds
            if stagnant_seconds >= self._active_no_progress_max_cycles * self._runtime_probe_threshold:
                reason = "runtime_no_progress"
    elif result.state == "probe_unavailable":
        # Transport/app request failure is not Bot unresponsiveness. Predeploy
        # and single-server deployments can make the probe read path itself
        # intermittently unavailable; therefore this state must never consume
        # recovery attempts or advance the task's failure state.
        current["unknown"] = 0
        current["no_progress"] = 0
        current["terminal_success_observed_at"] = None
        current["probe_unavailable"] = (
            current.get("probe_unavailable", 0) + 1
        )
        if current["probe_unavailable"] >= self._probe_unavailable_max_cycles:
            return recovery_policy.relay_probe_unavailable_exhaustion_patch(
                node,
                current,
                max_probe_cycles=self._probe_unavailable_max_cycles,
                detail=detail,
                elapsed=elapsed,
                sla=sla,
            )
    elif result.state == "unknown":
        current["unknown"] += 1
        current["no_progress"] = 0
        current["terminal_success_observed_at"] = None
        current["probe_unavailable"] = 0
        if current["unknown"] >= self._runtime_retry_threshold:
            reason = "runtime_unresponsive"

    if reason is None:
        logger.info(
            "[task][harness] runtime probe observed task=%s node=%s state=%s "
            "status=%s source=%s progressed=%s no_progress=%s elapsed=%.0fs",
            task_id, node.node_id, result.state, result.status, result.source,
            result.progressed,
            current.get("no_progress", 0) if result.state == "active" else 0,
            elapsed,
        )
        return None

    logger.warning(
        "[task][harness] relay runtime recovery task=%s node=%s reason=%s state=%s "
        "status=%s source=%s elapsed=%.0fs sla=%.0fs",
        task_id, node.node_id, reason, result.state, result.status,
        result.source, elapsed, sla,
    )
    if attempts < self._relay_resume_max_attempts:
        error_msg = None
        channel = None
        try:
            if self._relay_recovery is None:
                raise RelaySessionRecoveryError("relay recovery service is not configured")
            resumed = await self._relay_recovery.resume(
                node, resume_step=resume_step
            )
            channel = resumed.channel
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, RelaySessionPreconditionError):
                return recovery_policy.relay_session_precondition_failure(
                    exc, node, current,
                    max_attempts=self._relay_resume_max_attempts,
                    reason=reason, detail=detail, elapsed=elapsed, sla=sla,
                )
            return recovery_policy.relay_session_send_failure(
                exc, node, current,
                max_send_failures=self._relay_resume_max_send_failures,
                reason=reason, detail=detail, elapsed=elapsed, sla=sla,
            )
        current["last_activity_at"] = now
        current["resume_quiet_until"] = now + self._runtime_probe_threshold
        current["resume_attempts"] = attempts + 1
        current["resume_send_failures"] = 0
        current["resume_precondition_failures"] = 0
        # Clear the state-specific observation clock after a delivered resume.
        # The heartbeat baseline is also cleared so the prompt itself cannot
        # be counted as fresh progress; attempts remain cumulative.
        current["terminal_success_observed_at"] = None
        current["cursor"] = None
        current["unknown"] = 0
        current["no_progress"] = 0
        current["success_unreported"] = 0
        current["probe_unavailable"] = 0
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node.node_id,
                extend_props_patch={
                    "relay_harness_resume_attempts": attempts + 1,
                    **(
                        {"relay_harness_resume_run_id": resumed.dispatch_handle}
                        if resumed.dispatch_handle
                        else {}
                    ),
                    "harness_probe": {
                        **detail,
                        "reason": reason,
                        "elapsed_seconds": elapsed,
                        "sla_seconds": sla,
                        "unknown_count": current.get("unknown", 0),
                        "no_progress_count": current.get("no_progress", 0),
                        "success_unreported_count": current.get(
                            "success_unreported", 0
                        ),
                        "resume_attempts": attempts + 1,
                        "resume_channel": channel,
                        "resume_error": error_msg,
                    },
                },
            )
        )
        emit_relay_resume_trajectory(
            self._task_context_service,
            task_id,
            node,
            attempt=attempts + 1,
            reason=reason,
            channel=channel,
            error_msg=error_msg,
            terminal=False,
        )
        if error_msg is None:
            current["unknown"] = 0
            current["no_progress"] = 0
            current["success_unreported"] = 0
        return None

    if reason == "runtime_no_progress":
        # Silence cannot prove a long-running business call failed. Exhausting
        # continuation nudges must not turn an active runtime into task failure.
        return None

    return recovery_policy.relay_resume_exhaustion_patch(
        node,
        current,
        max_attempts=self._relay_resume_max_attempts,
        reason=reason,
        detail=detail,
        elapsed=elapsed,
        sla=sla,
    )
