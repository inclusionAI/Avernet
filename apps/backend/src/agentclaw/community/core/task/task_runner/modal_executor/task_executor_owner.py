"""Owner and human-observer lookup shared by TaskExecutor delivery modes."""

from __future__ import annotations

import logging

from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

logger = logging.getLogger(__name__)


class TaskExecutorOwnerMixin:
    def _singlebot_2_group_enabled(self, task_id: str) -> bool:
        """Read the task's frozen observer-group switch; preserve legacy default."""
        if self._graph is None:
            logger.info("[task][task-executor] singlebot_2_group 开关:graph 未接 → 默认 True(走旁路) task=%s", task_id)
            return True
        try:
            snapshot = self._graph.query_task_dashboard(task_id)
        except Exception:  # noqa: BLE001 graph 不可用 → 默认走旁路
            logger.warning("[task][task-executor] singlebot_2_group 开关:graph 查询失败 → 默认 True task=%s", task_id)
            return True
        cfg = (getattr(snapshot, "extend_props", None) or {}).get("execution_config") or {}
        if not isinstance(cfg, dict):
            logger.info("[task][task-executor] singlebot_2_group 开关:execution_config 非 dict → 默认 True task=%s", task_id)
            return True
        val = cfg.get("singlebot_2_group", True)
        enabled = val if isinstance(val, bool) else str(val).lower() not in ("false", "0", "no", "none", "")
        logger.info(
            "[task][task-executor] singlebot_2_group 开关:execution_config.singlebot_2_group=%s → enabled=%s task=%s",
            val, enabled, task_id,
        )
        return enabled

    def _resolve_graph_owner_user_id(self, task_id: str) -> str | None:
        """Resolve the Human task owner, not the executing Bot's owner."""
        if not task_id or self._graph is None:
            return None
        try:
            snapshot = self._graph.query_task_dashboard(task_id)
        except Exception:  # noqa: BLE001 graph 不可用/查询失败 → 不阻断建群
            logger.warning("[task][task_executor] resolve graph owner_user_id 查询失败 task=%s", task_id)
            return None
        owner = (getattr(snapshot, "extend_props", None) or {}).get("owner_user_id")
        logger.info("[task][task_executor] resolve_graph_owner_user_id task=%s owner=%s", task_id, owner)
        return str(owner) if owner else None

    def _resolve_owner_user_id(self, gf: GroupFormation) -> str | None:
        """Explicit Human owner first, then derive from the task graph."""
        explicit = gf.extend_props.get("owner_user_id")
        if explicit:
            logger.info(
                "[task][task_executor] resolve_owner_user_id 命中 gf.extend_props[owner_user_id] owner=%s",
                explicit,
            )
            return str(explicit)
        loop_task_id = gf.extend_props.get("loop_task_id") or ""
        task_id = (loop_task_id.split("::", 1)[0] if loop_task_id else "") or (
            gf.extend_props.get("task_id") or ""
        )
        owner = self._resolve_graph_owner_user_id(task_id)
        if owner is None:
            logger.info(
                "[task][task_executor] resolve_owner_user_id 无来源(owner_user_id/loop_task_id/task_id 均无或 graph 无 owner)→ 不拉人类观察者"
            )
        return owner
