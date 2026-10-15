"""Durable Relay handoff methods for the TaskService facade."""

from __future__ import annotations

import logging

from agentclaw.community.core.task.domain.errors import DispatchInProgress, TaskStateError
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
        if node is None or node.status not in {Status.PENDING, Status.RUNNING}:
            raise TaskStateError("relay delivery target is not runnable")
        initial_status = node.status
        relay_token = node.run_info.extend_props.get("relay_token")
        if not relay_token:
            raise TaskStateError("relay delivery target has no run generation")
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
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "delivery",
                    "action_result": "retired_legacy_root_intent",
                    "run_mode": node.run_info.run_mode,
                    "assignee": node.run_info.assignee,
                },
                status_from=initial_status,
                status_to=node.status,
            )
            logger.warning(
                "retired legacy relay root delivery intent task=%s node=%s",
                task_id,
                node_id,
            )
            return node
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
            self._graph.append_action_event(
                task_id,
                node_id,
                NodeAction.DISPATCH,
                {
                    "phase": "delivery",
                    "action_result": "delivered",
                    "run_mode": delivered.run_info.run_mode,
                    "assignee": delivered.run_info.assignee,
                },
                status_from=initial_status,
                status_to=delivered.status,
            )
            return delivered
        except Exception as exc:
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
                    "action_result": "failed",
                    "run_mode": node.run_info.run_mode,
                    "assignee": node.run_info.assignee,
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                },
                status_from=initial_status,
                status_to=node.status,
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
