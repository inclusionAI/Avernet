"""Relay-specific TaskExecutor delivery helpers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from agentclaw.community.core.task.domain.models import TaskNode

logger = logging.getLogger(__name__)


class TaskExecutorRelayMixin:
    """Deliver Relay continuations without re-running completed work."""


    async def _dispatch_bbs(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        """Start BBS selection, scoped to the Relay node when applicable."""
        if node.run_info.extend_props.get("relay_token") and not self._api_base_url:
            logger.error("relay BBS dispatch requires a configured Backend URL")
            return False
        if self._graph is None:
            logger.error(
                "[task][bbs_mode] dispatch failed: graph missing task=%s node=%s",
                node.task_id, node.node_id,
            )
            return False
        persisted_graph = self._graph.query_task_dashboard(node.task_id)
        if persisted_graph.is_relay and node.node_id == node.task_id:
            logger.error(
                "[task][bbs_mode] reject Relay root BBS dispatch task=%s node=%s",
                node.task_id, node.node_id,
            )
            return False
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
