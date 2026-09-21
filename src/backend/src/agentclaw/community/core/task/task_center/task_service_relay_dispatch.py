"""Relay dispatch ticket and Runner delivery operations."""

from __future__ import annotations

import logging
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status, TaskNodePatch
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)

logger = logging.getLogger("task.relay.search")


class TaskServiceRelayDispatchMixin:
    async def dispatch_task(
        self,
        *,
        task_id: str,
        origin_node_id: str,
        target_node_id: str,
        holder_id: str,
        relay_turn: str,
        dispatch_id: str,
    ) -> dict[str, Any]:
        relay = self._relay()
        _, node = self._relay_node(task_id, target_node_id)
        dispatch_state = str(
            node.run_info.extend_props.get("relay_dispatch_state") or ""
        )
        if node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id:
            if dispatch_state == "DELIVERED" or (
                dispatch_state == "DELIVERING"
                and node.status == Status.RUNNING
            ):
                return {"ok": True, "idempotent": True, "node_id": target_node_id}
        if node.run_info.extend_props.get("relay_planned_by") != origin_node_id:
            raise TaskStateError("relay dispatch target was not planned by origin node")
        if node.status != Status.PENDING:
            raise TaskStateError(
                f"relay dispatch target must be PENDING node={target_node_id}"
            )
        if node.run_info.run_mode not in {"single_bot", "coop_group"}:
            raise TaskStateError(
                "relay dispatch requires a persisted dispatch decision"
            )
        if (
            node.run_info.extend_props.get("relay_dispatch_id") == dispatch_id
            and dispatch_state == "DELIVERING"
        ):
            # The prior attempt may have consumed its ticket before the delivery
            # outcome was persisted. Restore the same ticket so retrying this
            # dispatch_id re-delivers rather than returning a false idempotent.
            try:
                self._report_relay_turn(
                    "RELAY_TURN_REOPEN",
                    task_id=task_id,
                    node_id=origin_node_id,
                    holder_id=holder_id,
                    token=relay_turn,
                )
            except TaskStateError:
                # The ticket can already be GRANTED (for example after an earlier
                # failed delivery). The normal require below still validates it.
                pass
        try:
            turn_node_id = relay.require(task_id, origin_node_id, holder_id, relay_turn)
        except TaskStateError as exc:
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="turn_invalid",
                error_type=ReasonCatalog.RELAY,
                error_msg=str(exc),
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "relay_turn_prefix": relay_turn[:8]},
            )
            raise
        if turn_node_id != origin_node_id:
            raise TaskStateError("relay dispatch origin does not own current turn")
        formation = None
        if node.run_info.run_mode == "coop_group":
            raw = node.run_info.extend_props.get("pending_group_formation") or {}
            formation = GroupFormation.from_dict(raw)
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=target_node_id,
                extend_props_patch={
                    "relay_dispatch_id": dispatch_id,
                    "relay_dispatch_state": "DELIVERING",
                },
            )
        )
        self._report_relay_turn(
            "RELAY_TURN_CONSUME",
            task_id=task_id,
            node_id=origin_node_id,
            holder_id=holder_id,
            token=relay_turn,
        )
        try:
            extend_patch: dict[str, Any] = {}
            if formation is not None:
                group_id = await self._relay_adapter.runner.form_coop_group(formation)
                extend_patch["group_id"] = group_id
            if extend_patch:
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=target_node_id,
                        extend_props_patch=extend_patch,
                    )
                )
            refreshed = self._relay_node(task_id, target_node_id)[1]
            delivered = bool(
                (await self._relay_adapter.runner.start_run([refreshed]))[0]
            )
        except Exception:
            logger.exception(
                "[task][relay] dispatch failed task=%s origin=%s target=%s",
                task_id,
                origin_node_id,
                target_node_id,
            )
            delivered = False
        if not delivered:
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=target_node_id,
                    failure_reason="下一棒 Runner 派发失败",
                    extend_props_patch={
                        "relay_dispatch_id": None,
                        "relay_dispatch_state": None,
                        "group_id": None,
                    },
                )
            )
            self._report_relay_turn(
                "RELAY_TURN_REOPEN",
                task_id=task_id,
                node_id=origin_node_id,
                holder_id=holder_id,
                token=relay_turn,
            )
            self._emit_relay(
                task_id=task_id,
                node_id=target_node_id,
                action_result="dispatch_failed_reopen",
                error_type=ReasonCatalog.RELAY,
                error_msg=f"relay dispatch failed node={target_node_id}",
                status_from=Status.PENDING,
                status_to=Status.PENDING,
                attempt=self._relay_attempt(task_id),
                ext_info={"holder_id": holder_id, "dispatch_id": dispatch_id},
            )
            raise TaskStateError(f"relay dispatch failed node={target_node_id}")
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=target_node_id,
                status=Status.RUNNING,
                progress_reason="下一棒 Runner 已完成实际投递",
                extend_props_patch={"relay_dispatch_state": "DELIVERED"},
            )
        )
        refreshed = self._relay_node(task_id, target_node_id)[1]
        self._emit_relay(
            task_id=task_id,
            node_id=target_node_id,
            action_result="dispatch",
            status_from=Status.PENDING,
            status_to=Status.RUNNING,
            attempt=self._relay_attempt(task_id),
            ext_info={
                "holder_id": holder_id,
                "dispatch_id": dispatch_id,
                "run_mode": refreshed.run_info.run_mode,
                "assignee": refreshed.run_info.assignee,
            },
        )
        return {
            "ok": True,
            "origin_node_id": origin_node_id,
            "target_node_id": target_node_id,
            "run_mode": refreshed.run_info.run_mode,
            "assignee": refreshed.run_info.assignee,
            "group_id": refreshed.run_info.extend_props.get("group_id"),
        }

