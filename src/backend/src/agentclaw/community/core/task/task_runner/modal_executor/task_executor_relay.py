"""Relay-specific TaskExecutor delivery helpers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from agentclaw.community.core.task.domain.models import TaskNode
from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import OpenApiError

logger = logging.getLogger(__name__)


class TaskExecutorRelayMixin:
    """Deliver Relay continuations without re-running completed work."""

    def _relay_pending_target(self, node: TaskNode):
        """Return the successor waiting for phase-specific continuation, if any."""
        try:
            snapshot = self._graph.query_task_dashboard(node.task_id)
        except Exception as exc:  # noqa: BLE101 graph unavailable → continue generic
            logger.warning(
                "[task][task-executor] relay resume cannot read successor task=%s node=%s: %s",
                node.task_id, node.node_id, exc,
            )
            return None, None
        successors = {
            str(relation.dst_id)
            for relation in getattr(snapshot, "relations", [])
            if str(relation.src_id) == str(node.node_id)
        }
        target = next(
            (
                item
                for item in getattr(snapshot, "tasks", [])
                if str(item.node_id) in successors
                and item.status.name == "PENDING"
            ),
            None,
        )
        if target is None:
            return None, None
        return target.node_id, target.run_info.run_mode

    async def resume_relay_turn(self, node: TaskNode, relay_turn: str) -> bool:
        """Prompt the current Relay holder to continue from PLAN_RESULT."""
        holder_id = str(
            node.run_info.extend_props.get("relay_holder_id")
            or node.run_info.assignee
            or ""
        ).strip()
        if not holder_id or self._bot is None:
            logger.warning(
                "[task][task-executor] relay resume unavailable task=%s node=%s holder=%s bot_port=%s",
                node.task_id, node.node_id, holder_id, self._bot is not None,
            )
            return False
        pending_target_id, pending_run_mode = self._relay_pending_target(node)
        message = (
            "[RESUME_RELAY]\n"
            "你此前已成功上报 EXECUTION_RESULT，但尚未完成本棒接力闭环。"
            "不得重做业务执行、不得生成新的最终报告、不得修改任何前序节点。"
            "继续动作必须依据 pending_target_node_id 判断：\n"
            f"- pending_target_node_id 为空：先依据当前节点已有 output 和根目标计算 gap；"
            f"无 gap 则 POST PLAN_RESULT，请求顶层携带 relay_turn，payload 为 {{\"gaps\": [], \"next_task_spec\": null}}；"
            f"有 gap 则 POST PLAN_RESULT，请求顶层同样携带 relay_turn，payload 为 {{\"gaps\": [...], \"next_task_spec\": {{...}}}}。\n"
            f"- pending_target_node_id 非空：PLAN_RESULT 已成功，不得重新规划同一节点。"
            f"若 pending_run_mode 不是 single_bot 或 coop_group，继续 search 并通过 DISPATCH_RESULT 决策；"
            f"若 pending_run_mode 是 single_bot 或 coop_group，立即 POST {self._api_base_url}/api/v1/collaboration/tasks/dispatch，"
            f"请求必须包含 task_id、origin_node_id={node.node_id}、target_node_id=<pending_target_node_id>、"
            f"holder_id={holder_id}、relay_turn=<本次 relay_turn>、稳定且唯一的 dispatch_id，缺任一变量时停止等待恢复。\n"
            f"task_id={node.task_id}\nnode_id={node.node_id}\nholder_id={holder_id}"
            f"\nrelay_turn={relay_turn}\nbackend={self._api_base_url}"
            f"\npending_target_node_id={pending_target_id or ''}"
            f"\npending_run_mode={pending_run_mode or ''}"
        )
        try:
            sent = await self._bot.send_message(
                bot_id=holder_id,
                message=message,
                metadata={"biz_task_id": node.task_id, "relay_resume": True},
            )
        except OpenApiError as exc:
            logger.warning(
                "[task][task-executor] relay resume delivery failed task=%s node=%s holder=%s error=%s",
                node.task_id, node.node_id, holder_id, exc,
            )
            return False
        self._persist_dispatch_ids(
            node,
            session_id=sent.session_id,
            run_id=sent.run_id,
            exec_request_input=message,
        )
        return True

    async def _dispatch_bbs(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        """Start BBS selection, scoped to the Relay node when applicable."""
        if self._graph is None:
            logger.error(
                "[task][bbs_mode] dispatch failed: graph missing task=%s node=%s",
                node.task_id, node.node_id,
            )
            return False
        persisted_graph = self._graph.query_task_dashboard(node.task_id)
        if not any(current.node_id == node.node_id for current in persisted_graph.tasks):
            logger.error(
                "[task][bbs_mode] dispatch failed: node missing task=%s node=%s",
                node.task_id, node.node_id,
            )
            return False
        execution_graph = replace(
            persisted_graph,
            tasks=[node if current.node_id == node.node_id else current for current in persisted_graph.tasks],
        )
        async with sem:
            from agentclaw.community.core.task.task_runner.modal_executor import bbs_modal_executor

            config = execution_graph.extend_props.get("execution_config", {}) or {}
            await bbs_modal_executor.notify(
                execution_graph=execution_graph,
                bcn=self._bcn,
                bot=self._bot,
                graph=self._graph,
                backend_url=self._api_base_url,
                skill_name=bbs_modal_executor._BBS_SKILL_NAME,
                on_bbs_report=self._on_bbs_report,
                group_executor=self._bbs_execute_as_manager_worker_group,
                target_node_id=node.node_id if config.get("orchestration_mode") == "relay" else None,
            )
        return True
