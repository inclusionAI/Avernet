"""Durable Relay handoff methods for the TaskService facade."""

from __future__ import annotations

import json
import logging

from agentclaw.community.core.task.domain.errors import DispatchInProgress, TaskStateError
from agentclaw.community.core.task.domain.identity import (
    is_dispatchable_single_bot_assignee,
)
from agentclaw.community.core.task.domain.models import (
    NodeAction,
    Status,
    TaskNode,
    TaskNodePatch,
)

logger = logging.getLogger("task.delivery")

_DEFAULT_DELIVERY_LEASE_MS = 60_000
# BBS currently resolves the roster, collects bids, and awaits the winning Bot's
# reply before the single dispatch operation can acknowledge delivery. Keep the
# delivery claim alive long enough for that modal handoff instead of teaching the
# redriver about Bot execution time. Ten minutes bounds crash recovery without
# returning to the duplicate-dispatch behavior produced by the 60-second lease.
_BBS_DELIVERY_LEASE_MS = 600_000


_TERMINAL_NODE_STATUSES = {
    Status.SUCCESS,
    Status.DONE,
    Status.FAILED,
    Status.HUNG,
    Status.CANCELLED,
}


def _relay_delivery_fields(
    *,
    status: str,
    action_input: dict[str, object],
    action_result: str,
    status_from: Status,
    status_to: Status,
    boost_reason: str,
    error_msg: str | None = None,
) -> dict[str, object]:
    """S6 七字段(与轨迹事件表对齐);action_log 与写时发射共用的单一事实源。"""
    return {
        "action_type": "dispatch",
        "action_input": json.dumps(
            action_input,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "action_result": action_result,
        "status_from": status_from.value,
        "status_to": status_to.value,
        "boost_reason": boost_reason,
        "error_msg": error_msg,
    }


def _relay_delivery_trace(
    status: str,
    *,
    action_input: dict[str, object],
    action_result: str,
    status_from: Status,
    status_to: Status,
    boost_reason: str,
    error_msg: str | None = None,
) -> dict[str, object]:
    """Project S6 service-side facts into the shared step payload contract
    (``trace.steps[0].detail`` 即七字段,经 `_relay_delivery_fields` 单源产出)。

    This keeps the report's outer ``step/status/detail`` contract unchanged
    for action_log 消费方;trajectory 侧已改为**写时**落表行
    (``TaskServiceDeliveryMixin._emit_delivery_trajectory``),不再读时投影。
    """
    return {
        "trace": {
            "steps": [
                {
                    "step": "S6_DISPATCH",
                    "status": status,
                    "detail": _relay_delivery_fields(
                        status=status,
                        action_input=action_input,
                        action_result=action_result,
                        status_from=status_from,
                        status_to=status_to,
                        boost_reason=boost_reason,
                        error_msg=error_msg,
                    ),
                }
            ]
        }
    }


def _relay_delivery_input(task_id: str, node: TaskNode) -> dict[str, object]:
    extend_props = node.run_info.extend_props
    return {
        "task_id": task_id,
        "origin_node_id": extend_props.get("relay_planned_by"),
        "target_node_id": node.node_id,
        "run_mode": node.run_info.run_mode,
        "assignee": node.run_info.assignee,
        "actual_run_mode": extend_props.get("actual_run_mode"),
        "group_id": extend_props.get("group_id"),
        "session_id": extend_props.get("session_id"),
        "delivery_intake_status": node.status.value,
    }


def _relay_delivery_success_output(node: TaskNode) -> str:
    """Serialize the actual S6 output, not the dispatch state transition."""
    extend_props = node.run_info.extend_props
    return json.dumps(
        {
            "status": "completed",
            "dispatched": True,
            "target_node_id": node.node_id,
            "run_mode": node.run_info.run_mode,
            "assignee": node.run_info.assignee,
            "actual_run_mode": extend_props.get("actual_run_mode"),
            "group_id": extend_props.get("group_id"),
            "session_id": extend_props.get("session_id"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _relay_delivery_failure_output() -> str:
    """Keep S6 failure output separate from ``error_msg`` failure cause."""
    return json.dumps(
        {"status": "failed", "dispatched": False},
        ensure_ascii=False,
        sort_keys=True,
    )


def _relay_delivery_legacy_root_output() -> str:
    return json.dumps(
        {"status": "completed", "retired_legacy_root_intent": True},
        ensure_ascii=False,
        sort_keys=True,
    )


def _emit_delivery_trajectory(
    service: "TaskServiceDeliveryMixin",
    task_id: str,
    node_id: str,
    *,
    status: str,
    action_input: dict[str, object],
    action_result: str,
    status_from: Status,
    status_to: Status,
    boost_reason: str,
    error_msg: str | None = None,
) -> None:
    """S6 写时发射:交付事实与 action_log 同步落一条 ``task_trajectory_events``
    行(action_type=dispatch,七字段单源出 `_relay_delivery_fields`;attempt=0
    与 append_action_event 默认一致,读侧投影按秒粒度签名去重归一)。
    fire-and-forget:吞异常不抛(``_task_context_service`` 未接线 no-op)。"""
    context = getattr(service, "_task_context_service", None)
    if context is None:
        return
    try:
        context.emit_trajectory_event(
            task_id,
            node_id,
            "dispatch",
            action_result=action_result,
            action_input=json.dumps(
                action_input, ensure_ascii=False, sort_keys=True,
            ),
            status_from=status_from,
            status_to=status_to,
            attempt=0,
            boost_reason=boost_reason,
            error_msg=error_msg,
            ext_info={"trace_step": True, "step": "S6_DISPATCH"},
        )
    except Exception:  # noqa: BLE001  观测旁路,吞而不抛
        logger.warning(
            "relay delivery trajectory emission failed task=%s node=%s",
            task_id,
            node_id,
            exc_info=True,
        )


class TaskServiceDeliveryMixin:
    async def dispatch_node(self, task_id: str, node_id: str) -> TaskNode:
        """S6: claim a persisted intent, dispatch its authoritative node once."""
        intents = self._delivery_intents
        if intents is None:
            raise TaskStateError("durable delivery intents are unavailable")
        graph = self._graph.query_task_dashboard(task_id)
        if not graph.is_relay:
            raise TaskStateError("node dispatch requires relay orchestration")
        node = next((item for item in graph.tasks if item.node_id == node_id), None)
        if node is not None and node.status in _TERMINAL_NODE_STATUSES:
            # A terminal baton cannot accept a new instruction, even when a
            # legacy completion path cleared its owner or missed the graph-save
            # settlement. Previously this cleanup was BBS-only, which left
            # terminal single_bot/coop_group intents to make the redriver noisy
            # forever.
            stale_token = node.run_info.extend_props.get("relay_token")
            if stale_token and intents.settle_delivery_intent(
                task_id, node_id, stale_token
            ):
                logger.warning(
                    "settled terminal relay delivery intent task=%s node=%s "
                    "run_mode=%s status=%s",
                    task_id,
                    node_id,
                    node.run_info.run_mode,
                    node.status.value,
                )
                return node
        if node is None or node.status not in {Status.PENDING, Status.RUNNING}:
            raise TaskStateError("relay delivery target is not runnable")
        initial_status = node.status
        relay_token = node.run_info.extend_props.get("relay_token")
        if not relay_token:
            raise TaskStateError("relay delivery target has no run generation")
        if (
            node.run_info.run_mode == "single_bot"
            and not is_dispatchable_single_bot_assignee(node.run_info.assignee)
        ):
            if intents.settle_delivery_intent(task_id, node_id, relay_token):
                logger.warning(
                    "settled invalid single_bot delivery intent task=%s node=%s",
                    task_id,
                    node_id,
                )
            raise TaskStateError("relay delivery target has an invalid assignee")
        lease_ms = (
            _BBS_DELIVERY_LEASE_MS
            if node.run_info.run_mode == "bbs"
            else _DEFAULT_DELIVERY_LEASE_MS
        )
        claimed_delivery = intents.claim_delivery_intent(
            task_id, node_id, relay_token, lease_ms=lease_ms
        )
        state = claimed_delivery[0]
        if state == "DELIVERED":
            return node
        if state == "DELIVERING":
            raise DispatchInProgress()
        if state != "CLAIMED" or claimed_delivery[1] is None:
            raise TaskStateError(f"relay delivery is not claimable: {state}")
        if node.run_info.extend_props.get("relay_root"):
            # Compatibility cleanup for roots created by the old protocol.
            # The first baton is now forwarded in the already-open Bot
            # conversation, so retrying this legacy intent through Runner would
            # create a duplicate session or permission-solving group forever.
            intents.mark_delivery_intent(
                task_id, node_id, claimed_delivery[1], delivered=True
            )
            legacy_root_output = _relay_delivery_legacy_root_output()
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "delivery",
                    "action_result": legacy_root_output,
                    "run_mode": node.run_info.run_mode,
                    "assignee": node.run_info.assignee,
                    **_relay_delivery_trace(
                        status="completed",
                        action_input=_relay_delivery_input(task_id, node),
                        action_result=legacy_root_output,
                        status_from=initial_status,
                        status_to=node.status,
                        boost_reason="legacy root intent retired to avoid duplicate delivery",
                    ),
                },
                status_from=initial_status,
                status_to=node.status,
            )
            _emit_delivery_trajectory(
                self, task_id, node_id,
                status="completed",
                action_input=_relay_delivery_input(task_id, node),
                action_result=legacy_root_output,
                status_from=initial_status,
                status_to=node.status,
                boost_reason="legacy root intent retired to avoid duplicate delivery",
            )
            logger.warning(
                "retired legacy relay root delivery intent task=%s node=%s",
                task_id,
                node_id,
            )
            return node
        dispatch_input = _relay_delivery_input(task_id, node)
        dispatch_output: str
        try:
            if node.status == Status.PENDING and node.run_info.run_mode != "bbs":
                self._report_node_patch(TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    status=Status.RUNNING,
                ))
                graph = self._graph.query_task_dashboard(task_id)
                node = next(item for item in graph.tasks if item.node_id == node_id)
            dispatcher = self._relay_adapter.dispatcher
            if dispatcher is None:
                raise TaskStateError("relay dispatcher is not configured")
            delivered = await dispatcher.dispatch(node)
            if not isinstance(delivered, TaskNode):
                raise TaskStateError("relay dispatcher did not return one TaskNode")
            intents.mark_delivery_intent(
                task_id, node_id, claimed_delivery[1], delivered=True
            )
            dispatch_output = _relay_delivery_success_output(delivered)
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "delivery",
                    "action_result": dispatch_output,
                    "run_mode": delivered.run_info.run_mode,
                    "assignee": delivered.run_info.assignee,
                    **_relay_delivery_trace(
                        status="completed",
                        action_input=dispatch_input,
                        action_result=dispatch_output,
                        status_from=initial_status,
                        status_to=delivered.status,
                        boost_reason="relay handoff accepted by the selected runtime",
                    ),
                },
                status_from=initial_status,
                status_to=delivered.status,
            )
            _emit_delivery_trajectory(
                self, task_id, node_id,
                status="completed",
                action_input=dispatch_input,
                action_result=dispatch_output,
                status_from=initial_status,
                status_to=delivered.status,
                boost_reason="relay handoff accepted by the selected runtime",
            )
            return delivered
        except Exception as exc:
            dispatch_output = _relay_delivery_failure_output()
            intents.mark_delivery_intent(
                task_id, node_id, claimed_delivery[1], delivered=False,
                error=str(exc),
            )
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "delivery",
                    "action_result": dispatch_output,
                    "run_mode": node.run_info.run_mode,
                    "assignee": node.run_info.assignee,
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                    **_relay_delivery_trace(
                        status="failed",
                        action_input=dispatch_input,
                        action_result=dispatch_output,
                        status_from=initial_status,
                        status_to=node.status,
                        boost_reason="relay handoff rejected by the selected runtime",
                        error_msg=f"{type(exc).__name__}: {exc}",
                    ),
                },
                status_from=initial_status,
                status_to=node.status,
            )
            _emit_delivery_trajectory(
                self, task_id, node_id,
                status="failed",
                action_input=dispatch_input,
                action_result=dispatch_output,
                status_from=initial_status,
                status_to=node.status,
                boost_reason="relay handoff rejected by the selected runtime",
                error_msg=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def dispatch_pending_intents(self, *, limit: int = 100) -> int:
        intents = self._delivery_intents
        if intents is None:
            return 0
        delivered = 0
        for task_id, node_id in intents.list_pending_delivery_intents(limit=limit):
            try:
                await self.dispatch_node(task_id, node_id)
                delivered += 1
            except DispatchInProgress:
                # A list snapshot can race another caller's atomic claim. The
                # claim contract is doing exactly its job: one delivery at a
                # time; the redriver will see the same intent on a later pass.
                logger.info(
                    "relay delivery already in progress task=%s node=%s",
                    task_id,
                    node_id,
                )
            except Exception:
                logger.exception("relay delivery retry failed task=%s node=%s", task_id, node_id)
        return delivered
