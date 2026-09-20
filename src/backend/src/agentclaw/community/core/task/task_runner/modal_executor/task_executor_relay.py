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
        message = (
            "[RESUME_RELAY]\n"
            "你此前已成功上报 EXECUTION_RESULT，但尚未完成本棒接力闭环。"
            "不得重做业务执行、不得生成新的最终报告、不得修改任何前序节点。"
            "立即使用以下新的 relay_turn 从 PLAN_RESULT 开始：先依据当前节点已有 output "
            "和根目标计算 gap；无 gap 则 POST PLAN_RESULT(has_gap=false, children=[])；"
            "有 gap 则 POST PLAN_RESULT，再 search → DISPATCH_RESULT → dispatch/BBS。"
            f"\ntask_id={node.task_id}\nnode_id={node.node_id}\nholder_id={holder_id}"
            f"\nrelay_turn={relay_turn}\nbackend={self._api_base_url}"
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
